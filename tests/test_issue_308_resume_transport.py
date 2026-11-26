import base64
import hashlib
import json
import secrets
import sqlite3
import chess
import pytest
from test_issue_314_worker_update_lifecycle_e2e import staging
from remote_worker_phone_sandbox import MODEL_SHA
from remote_worker_inference_delivery import WorkerInferenceDelivery
from remote_worker_update_pause import WorkerUpdatePause
from remote_worker_resume_transport import ResumeTransport
from remote_worker_tokens import WorkerTokenStore


@pytest.mark.parametrize("fault", ["corrupt", "missing_stop", "changed_lease", "oversize", "consumed"])
def test_authorized_checkpoint_delivery(staging, fault):
    db, worker, post = staging
    post('ready', model_sha256=MODEL_SHA)
    job = dict(version=3, job_id='job_'+secrets.token_hex(12), fen=chess.STARTING_FEN, model_sha256=MODEL_SHA, legal_moves=sorted(m.uci() for m in chess.Board().legal_moves), max_tokens=32, timeout_seconds=600, lease_token=secrets.token_hex(32))
    WorkerInferenceDelivery(db).assign_from_arena(worker, job)
    pause = WorkerUpdatePause(db)
    rid = pause.request(worker, job['job_id'])
    post('native-stop-report', job_id=job['job_id'], request_id=rid, lease_token=job['lease_token'])
    pause.acknowledge(worker, job['job_id'], rid, worker_stopped=True, checkpoint_verified=True, lease_token_sha256=hashlib.sha256(job['lease_token'].encode()).hexdigest())
    cp = dict(schema_version=1, job_id=job['job_id'], resume_mode='restart', payload={'fen':job['fen']})
    hold = secrets.token_hex(32)
    saved = pause.finalize_verified_pause(worker, job['job_id'], rid, hold_token=hold, checkpoint=cp, worker_stopped_verified=True, checkpoint_verifier=lambda c:c==cp)
    fields = dict(job_id=job['job_id'], request_id=rid, checkpoint_sha256=saved['sha256'])
    transport = ResumeTransport(db)
    proof = dict(installed_identity_verified=True, stopped_engine_verified=True, checkpoint_verifier=lambda c:c==cp)
    with pytest.raises(ValueError):
        transport.authorize(worker, **proof)
    post('update-release', hold_token=hold)
    post('resume-checkpoint', 409, **fields)
    with pytest.raises(ValueError):
        transport.authorize(worker, **dict(proof, installed_identity_verified=False))
    assert 'delivery_offer' not in post('update-resume-status')
    transport.authorize(worker, **proof)
    status = post('update-resume-status')
    assert status['delivery_offer'] == dict(version=1, **fields)
    assert status['resume_available'] is False
    post('resume-checkpoint', 401, **fields, token='invalid')
    other = 'android_other_12345678'
    other_token = WorkerTokenStore(db).issue(other, ttl_seconds=600)
    post('resume-checkpoint', 409, **fields, worker_id=other, token=other_token)
    for k,v in [('job_id','job_12345678'),('request_id','0'*32),('checkpoint_sha256','0'*64)]:
        post('resume-checkpoint', 409, **dict(fields, **{k:v}))
    first = post('resume-checkpoint', **fields)
    second = post('resume-checkpoint', **fields)
    raw = base64.b64decode(first['checkpoint_base64'], validate=True)
    assert raw == base64.b64decode(second['checkpoint_base64'], validate=True)
    assert len(raw)==first['checkpoint_size'] and hashlib.sha256(raw).hexdigest()==saved['sha256']
    assert json.loads(raw)==cp and first['resume_available'] is False
    assert post('job-poll')['state']=='blocked'
    assert pause.jobs.state(worker)=='unknown'
    with sqlite3.connect(db) as con:
        con.execute('UPDATE worker_resume_delivery SET expires_at=0')
    post('resume-checkpoint', 409, **fields)
    assert 'delivery_offer' not in post('update-resume-status')
    transport.authorize(worker, **proof)
    with sqlite3.connect(db) as con:
        if fault == "corrupt":
            con.execute("UPDATE worker_update_resume SET checkpoint_json='{}'")
        elif fault == "missing_stop":
            con.execute("DELETE FROM worker_native_stop_reports WHERE worker_id=?", (worker,))
        elif fault == "changed_lease":
            changed = dict(job, lease_token=secrets.token_hex(32))
            con.execute("UPDATE worker_inference_assignments SET job_json=? WHERE worker_id=?", (json.dumps(changed), worker))
        elif fault == "oversize":
            large = json.dumps(dict(cp, payload={"large": "x" * (1024 * 1024)}))
            large_hash = hashlib.sha256(large.encode()).hexdigest()
            con.execute("UPDATE worker_update_resume SET checkpoint_json=?, checkpoint_sha256=?", (large, large_hash))
            con.execute("UPDATE worker_resume_delivery SET digest=?", (large_hash,))
            fields["checkpoint_sha256"] = large_hash
        elif fault == "consumed":
            con.execute("DELETE FROM worker_update_resume WHERE worker_id=?", (worker,))
    post('resume-checkpoint', 409, **fields)
    # The pre-existing status API rejects corrupted checkpoint bytes with 400.
    assert 'delivery_offer' not in post('update-resume-status', 400 if fault == 'corrupt' else 200)
    assert pause.jobs.state(worker)=='unknown'


def test_resume_readiness_ignores_stale_identity_and_requests():
    import shutil
    import subprocess
    from pathlib import Path
    if not shutil.which('node'):
        pytest.skip('Node.js unavailable')
    root = Path(__file__).resolve().parents[1]
    script = r"""
const assert=require('assert'), fs=require('fs'), vm=require('vm');
const html=fs.readFileSync('remote_worker_gui/public/index.html','utf8');
const start=html.indexOf('let resumeReadinessState=null;');
const end=html.indexOf("document.getElementById('resumeWorkerGame').addEventListener",start);
assert(start>=0 && end>start);
const nodes=Object.fromEntries(['server','workerId','resumeWorkerGame','resumeWorkerStatus'].map(id=>[id,{value:'',disabled:true,textContent:'',addEventListener(type,cb){this[type]=cb;}}]));
nodes.server.value='https://arena.example';nodes.workerId.value='worker-a';
const calls=[];
const ctx={document:{getElementById:id=>nodes[id]},phoneWorkerEnabled:false,window:{__TAURI__:{core:{invoke:(name,args)=>new Promise(resolve=>calls.push({name,args,resolve}))}}}};
vm.createContext(ctx);vm.runInContext(html.slice(start,end),ctx);
const tick=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
 const first=ctx.refreshWorkerResumeReadiness();
 nodes.workerId.value='worker-b';nodes.workerId.input();
 calls[0].resolve('awaiting_local_engine');await first;
 assert.equal(calls.length,1);assert(nodes.resumeWorkerGame.disabled);
 const second=ctx.refreshWorkerResumeReadiness();calls[1].resolve('awaiting_local_engine');await tick();
 assert.equal(calls[2].name,'stage_worker_resume_checkpoint');assert.strictEqual(calls[1].args,calls[2].args);
 assert.equal(calls[2].args.workerId,'worker-b');
 nodes.server.value='https://other.example';nodes.server.input();
 calls[2].resolve('checkpoint_stored');await second;
 assert(nodes.resumeWorkerGame.disabled);assert(!nodes.resumeWorkerStatus.textContent.includes('Checkpoint zapisany'));
 const older=ctx.refreshWorkerResumeReadiness(), newer=ctx.refreshWorkerResumeReadiness();
 calls[4].resolve('no_checkpoint');await tick();calls[5].resolve(true);await newer;
 assert(!nodes.resumeWorkerGame.disabled);
 calls[3].resolve('awaiting_local_engine');await older;
 assert.equal(calls.length,6);assert(!nodes.resumeWorkerGame.disabled);
 nodes.workerId.value='worker-c';nodes.workerId.input();assert(nodes.resumeWorkerGame.disabled);
})().catch(e=>{console.error(e);process.exitCode=1;});
"""
    subprocess.run(['node', '-e', script], cwd=root, check=True, timeout=10)
