"""Dedicated HTTPS feedback must not overwrite token or stale-session state."""
from pathlib import Path
import shutil
import subprocess
import pytest

UI = Path(__file__).resolve().parents[1] / 'remote_worker_gui/public/index.html'


def test_connection_feedback_next_to_button():
    html = UI.read_text()
    assert html.index('id="pair"') < html.index('id="connectionFeedback"') < html.index('id="previewJob"')
    assert 'aria-describedby="connectionFeedback"' in html
    assert 'role="status" aria-live="polite" aria-atomic="true" data-state="idle"' in html
    assert 'id="connectionFeedbackIcon" aria-hidden="true"' in html
    assert 'Nie sprawdzono jeszcze połączenia.' in html
    assert 'flex:0 0 100%' in html


def test_connection_feedback_real_handler():
    if not shutil.which('node'):
        pytest.skip('Node.js required')
    script = r"""
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const html=fs.readFileSync(process.argv[1],'utf8');
function section(a,b){const start=html.indexOf(a),end=html.indexOf(b,start);assert.ok(start>=0&&end>start);return html.slice(start,end);}
const source=section('let verifiedJobPreviewConnection=null;','async function refreshTokenState()')+
section("document.getElementById('pair').addEventListener('click'","document.getElementById('previewJob').addEventListener('click'");
function setup({native=true,persist=true}={}){
 const fields=new Map(),pending=[],calls=[];
 const get=id=>{if(!fields.has(id))fields.set(id,{value:'',textContent:'',disabled:false,dataset:{},events:{},addEventListener(n,f){this.events[n]=f;}});return fields.get(id);};
 get('server').value='https://old.example';get('workerId').value='worker_old_01';
 get('backendStatus').textContent='Unrelated token status';
 const context=vm.createContext({document:{getElementById:get},window:native?{__TAURI__:{core:{invoke:(name,args)=>{calls.push(name);return new Promise((resolve,reject)=>pending.push({resolve,reject}));}}}}:{},renderWorkerArenaBoard(){},refreshPhoneButtons(){},persistWorkerIdentity:()=>persist,phoneInferenceStatus:get('localInferenceStatus')});
 vm.runInContext('let connectionRevision=0,resumeReadinessState=null,phonePaired=false,phoneSupported=false,phoneWorkerEnabled=false;'+source,context);
 return {get,pending,calls,run:s=>vm.runInContext(s,context),click:()=>get('pair').events.click(),state:()=>get('connectionFeedback').dataset.state};
}
const tick=()=>new Promise(r=>setImmediate(r));
async function resolve(t,v){t.pending.shift().resolve(v);await tick();}
async function reject(t,v){t.pending.shift().reject(v);await tick();}
function check(t,state){assert.equal(t.state(),state);assert.ok(t.get('connectionFeedbackIcon').textContent);assert.ok(t.get('connectionFeedbackText').textContent);assert.equal(t.get('backendStatus').textContent,'Unrelated token status');}
(async()=>{
 let t=setup({native:false});await t.click();check(t,'error');
 t=setup();let work=t.click();check(t,'checking');assert.equal(t.get('pair').disabled,true);await t.click();assert.equal(t.calls.length,1);await resolve(t,false);await work;check(t,'missing');assert.equal(t.get('pair').disabled,false);
 t=setup();work=t.click();await resolve(t,true);await resolve(t,false);await work;check(t,'rejected');assert.equal(t.run('phonePaired'),false);
 t=setup();work=t.click();await resolve(t,true);await resolve(t,true);await resolve(t,true);await work;check(t,'ok');assert.equal(t.run('phonePaired'),true);assert.equal(t.get('previewJob').disabled,false);
 t=setup({persist:false});work=t.click();await resolve(t,true);await resolve(t,true);await resolve(t,true);await work;check(t,'error');assert.equal(t.run('phonePaired'),false);
 for(const err of ['Brak zapisanego tokenu','network','secret-should-not-be-shown']){
  t=setup();work=t.click();await resolve(t,true);await reject(t,err);await work;check(t,err==='Brak zapisanego tokenu'?'missing':'error');assert.equal(t.get('pair').disabled,false);assert.ok(!t.get('connectionFeedbackText').textContent.includes('secret-should-not-be-shown'));
 }
 for(const change of ['server','workerId','token'])for(const stage of [0,1,2])for(const fail of [false,true]){
  t=setup();work=t.click();if(stage>=1)await resolve(t,true);if(stage>=2)await resolve(t,true);
  if(change!=='token')t.get(change).value='changed';t.run('invalidateJobPreview()');
  const msg=t.get('connectionFeedbackText').textContent;
  if(fail)await reject(t,'late error');else await resolve(t,true);
  await work;check(t,'idle');assert.equal(t.get('connectionFeedbackText').textContent,msg);assert.equal(t.run('phonePaired'),false);assert.equal(t.get('previewJob').disabled,true);assert.equal(t.get('pair').disabled,false);
 }
})().catch(e=>{console.error(e);process.exitCode=1;});
"""
    subprocess.run(['node', '-e', script, str(UI)], check=True, timeout=15)
