"""Regression checks: delayed HTTPS checks cannot reactivate a stale Worker session."""
from pathlib import Path

UI = (Path(__file__).resolve().parents[1] /
      "remote_worker_gui/public/index.html")


def test_connection_changes_invalidate_previous_https_check():
    html = UI.read_text(encoding="utf-8")
    assert "let connectionRevision=0" in html
    assert "connectionRevision++" in html
    assert "const revision=connectionRevision" in html
    assert "if(revision!==connectionRevision ||" in html
    assert "server!==document.getElementById('server').value.trim()" in html
    assert "workerId!==document.getElementById('workerId').value.trim()" in html
    assert html.index("if(revision!==connectionRevision ||") < html.index(
        "verifiedJobPreviewConnection=ok?{server,workerId}:null")


def test_connection_check_is_single_flight_and_remains_manual():
    html = UI.read_text(encoding="utf-8")
    assert "if(checkingConnection)return;" in html
    assert "checkingConnection=true" in html
    assert "button.disabled=true" in html
    assert "checkingConnection=false" in html
    assert "button.disabled=false" in html
    assert "document.getElementById('pair').addEventListener('click'" in html
    # Polling inference/board is separate from manual authentication.
    assert "setInterval(" not in html.split("document.getElementById('pair').addEventListener('click'", 1)[1]
    assert "if(!phonePaired||!phoneWorkerEnabled||phoneWorkerRunning" in html
    assert "if(!phonePaired||workerJobPollInFlight||" in html
    assert "if(!phonePaired||workerBoardPollInFlight||" in html
    assert "phonePaired=false;phoneWorkerEnabled=false;refreshPhoneButtons();" in html


def test_token_changes_revoke_pending_connection_before_secret_mutation():
    html = UI.read_text(encoding="utf-8")
    assert html.index("invalidateJobPreview(); // Invalidate any pending HTTPS") < html.index(
        "await invoke('store_worker_token'")
    assert html.index("invalidateJobPreview(); // Pending HTTPS") < html.index(
        "await invoke('forget_worker_token'")
    assert "verifiedJobPreviewConnection=null;" in html
    assert "document.getElementById('previewJob').disabled=true" in html


def test_delayed_worker_replies_cannot_restore_previous_identity():
    import shutil
    import subprocess
    import pytest
    if not shutil.which('node'):
        pytest.skip('Node.js required for asynchronous GUI test')
    script = r'''
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync(process.argv[1], 'utf8');
function section(start, end) {
 const a=html.indexOf(start), b=html.indexOf(end,a);
 assert.ok(a>=0 && b>a, start);
 return html.slice(a,b);
}
const source = [
 section('let connectionRevision=0;', 'setInterval(()=>{void processOnePhoneMove();}'),
 section('let workerJobPollInFlight=false;', 'setInterval(()=>{void refreshMyInferenceAssignment();}'),
 section('let workerBoardPollInFlight=false;', 'setInterval(()=>{void refreshMyWorkerBoard();}'),
 section('let resumeReadinessState=null;', "document.getElementById('checkUpdateSafety').addEventListener"),
 section('let verifiedJobPreviewConnection=null;', 'async function refreshTokenState()'),
].join('\n');
function setup() {
 const elements = new Map(), pending=[], calls=[], boards=[];
 const get=id=>{if(!elements.has(id))elements.set(id,{value:'',textContent:'',disabled:false,dataset:{},events:{},addEventListener(name,fn){this.events[name]=fn;}});return elements.get(id);};
 get('server').value='https://old.example';get('workerId').value='android_old_01';
 const context=vm.createContext({document:{getElementById:get,hidden:true,addEventListener(){}},window:{__TAURI__:{core:{invoke:(name,args)=>{calls.push({name,args});return new Promise((resolve,reject)=>pending.push({resolve,reject}));}}}},renderWorkerArenaBoard:view=>boards.push(view)});
 vm.runInContext(source,context);
 context.document.hidden=false; // Foreground after startup; isolate identity race from initial diagnostics.
 const run=s=>vm.runInContext(s,context);
 const change=kind=>{if(kind==='id')get('workerId').value='android_new_01';if(kind==='server')get('server').value='https://new.example';run('invalidateJobPreview()');};
 run('phonePaired=true;phoneSupported=true;phoneWorkerEnabled=true;resumeReadinessState="no_checkpoint"');
 return {get,pending,calls,boards,run,change};
}
const tick=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
 for(const kind of ['id','server','token']){
  for(const [fn,value,field] of [
   ['refreshMyInferenceAssignment()', {state:'assigned',job:{job_id:'old_private_job',model_sha256:'a'.repeat(64)}}, 'myAssignedGame'],
   ['refreshMyWorkerBoard()', {active:true,fen:'old_private_board'}, 'myWorkerBoardEmpty'],
   ['processOnePhoneMove()', {state:'received_not_applied'}, 'localInferenceStatus'],
   ['refreshWorkerResumeReadiness()', 'no_checkpoint', 'resumeWorkerStatus']
  ]){
   for(const fail of [false,true]){
    const t=setup(), work=t.run(fn);
    assert.equal(t.pending.length,1);
    t.change(kind);
    const previous=t.get(field).textContent, boards=t.boards.length;
    if(fail)t.pending[0].reject(Error('old error'));else t.pending[0].resolve(value);
    await work;
    assert.equal(t.get(field).textContent,previous);
    assert.equal(t.boards.length,boards);
    assert.equal(t.run('phonePaired'),false);
    assert.equal(t.run('phoneWorkerEnabled'),false);
    assert.equal(t.run('resumeReadinessState'),null);
    assert.equal(t.get('resumeWorkerGame').disabled,true);
   }
  }
  const replies=['no_checkpoint',true,true,'ready','idle_confirmed'];
  for(let stage=0;stage<replies.length;stage++){
   const t=setup();t.run('phoneWorkerEnabled=false');
   const work=t.get('resumeWorkerGame').events.click();
   for(let i=0;i<stage;i++){assert.equal(t.pending.length,i+1);t.pending[i].resolve(replies[i]);await tick();}
   assert.equal(t.pending.length,stage+1);
   t.change(kind);t.pending[stage].resolve(replies[stage]);await work;
   assert.equal(t.calls.length,stage+1);
   assert.equal(t.run('phonePaired'),false);
   assert.equal(t.run('phoneWorkerEnabled'),false);
   assert.equal(t.get('resumeWorkerGame').disabled,true);
  }
 }
 // Positive control: an unchanged identity can receive an assigned job.
 const t=setup(), work=t.run('refreshMyInferenceAssignment()');
 t.pending[0].resolve({state:'assigned',job:{job_id:'current_job',model_sha256:'b'.repeat(64)}});await work;
 assert.equal(t.get('myAssignedGame').textContent,'current_job');
 console.log('40 asynchronous scenarios passed');
})().catch(err=>{console.error(err);process.exitCode=1;});
'''
    subprocess.run(['node', '-e', script, str(UI)], check=True, timeout=15)


def test_update_channel_revision_fences_download_and_metadata():
    import shutil
    import subprocess
    import pytest
    if not shutil.which("node"):
        pytest.skip("Node.js required")
    script = r"""
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const html=fs.readFileSync(process.argv[1],'utf8');
const source=html.slice(html.indexOf('const updateToggle='),html.indexOf('async function reconcileInstalledApkAtStartup'));
function setup(){
 const elements=new Map(),pending=[];
 const get=id=>{if(!elements.has(id))elements.set(id,{value:'',textContent:'',hidden:true,disabled:false,style:{},events:{},removeAttribute(){},addEventListener(n,f){this.events[n]=f;}});return elements.get(id)};
 const ctx=vm.createContext({document:{getElementById:get},localStorage:{getItem(){return null},setItem(){}},window:{__TAURI__:{core:{invoke(name){return new Promise((resolve,reject)=>pending.push({name,resolve,reject}))}}}}});
 vm.runInContext(source,ctx);
 return {get,pending,run:s=>vm.runInContext(s,ctx)};
}
const tick=()=>new Promise(r=>setImmediate(r));
(async()=>{
 for(const failure of [false,true]){
  const t=setup(),dl=t.get('downloadWorkerApk');
  dl.hidden=false;dl.disabled=false;
  const work=dl.events.click();
  assert.equal(t.pending[0].name,'download_worker_apk');
  t.get('updateChannel').value='pre-release';t.get('updateChannel').events.change();
  t.get('updateChannel').value='latest';t.get('updateChannel').events.change();
  const status=t.get('apkDownloadStatus').textContent;
  if(failure)t.pending[0].reject(Error('old download'));else t.pending[0].resolve();
  await work;
  assert.equal(t.get('apkDownloadStatus').textContent,status);
  assert.equal(t.get('installWorkerApk').hidden,true);
  assert.equal(t.pending.some(p=>p.name==='verify_downloaded_worker_apk'),false);
 }
 const t=setup(),dl=t.get('downloadWorkerApk');dl.hidden=false;dl.disabled=false;
 const work=dl.events.click();t.pending[0].resolve();await tick();
 assert.equal(t.pending[1].name,'verify_downloaded_worker_apk');
 t.get('updateChannel').value='pre-release';t.get('updateChannel').events.change();
 t.get('updateChannel').value='latest';t.get('updateChannel').events.change();
 t.pending[1].resolve({verified:true,installer_permission:true});await work;
 assert.equal(t.get('installWorkerApk').hidden,true);
 const u=setup();const check=u.run('checkForWorkerUpdate()');
 u.get('updateChannel').value='pre-release';u.get('updateChannel').events.change();
 u.get('updateChannel').value='latest';u.get('updateChannel').events.change();
 u.pending[0].resolve({update_available:true,apk_asset_available:true,latest_version:'worker-v9.9.9'});
 await check;
 assert.equal(u.get('downloadWorkerApk').hidden,true);
 assert.equal(u.pending.length,2);
})().catch(e=>{console.error(e);process.exitCode=1});
"""
    subprocess.run(["node", "-e", script, str(UI)], check=True, timeout=15)
