"""Loopback HTTP + durable coordinator lifecycle; device evidence is simulated."""
import hashlib
import json
import secrets
import sqlite3
import threading
from http.server import ThreadingHTTPServer
from urllib import request, error

import chess
import pytest
import server
import tournament_checkpoint
from remote_worker_phone_sandbox import MODEL_SHA, SANDBOX_TOURNAMENT_ID, prepare_sandbox
from remote_worker_tokens import WorkerTokenStore
from remote_worker_inference_delivery import WorkerInferenceDelivery
from remote_worker_update_pause import WorkerUpdatePause
from worker_backend import WORKER_POST_ROUTES


@pytest.fixture
def staging(tmp_path, monkeypatch):
    worker = 'android_e2e_12345678'
    source = tmp_path / 'token-source.sqlite3'
    token = WorkerTokenStore(source).issue(worker, ttl_seconds=600)
    db = tmp_path / 'stage' / 'arena.sqlite3'
    prepare_sandbox(db, source, worker)
    monkeypatch.setattr(server, 'DB', db)
    monkeypatch.setenv('ARENA_PHONE_WORKER_API_ENABLED', '1')
    monkeypatch.setenv('ARENA_PHONE_INFERENCE_ENABLED', '0')

    class StageHandler(server.Handler):
        def do_POST(self):
            if self.path not in WORKER_POST_ROUTES:
                self.send_json({'error': 'stage route not allowed'}, 404)
                return
            super().do_POST()

    api = ThreadingHTTPServer(('127.0.0.1', 0), StageHandler)
    thread = threading.Thread(target=api.serve_forever, daemon=True)
    thread.start()

    def post(route, expected=200, **fields):
        body = {'worker_id': worker, 'token': token, 'nonce': secrets.token_hex(16), **fields}
        req = request.Request(f'http://127.0.0.1:{api.server_port}/api/remote-workers/{route}',
                              json.dumps(body).encode(), {'Content-Type': 'application/json'}, method='POST')
        try:
            with request.urlopen(req, timeout=5) as response:
                status, value = response.status, json.load(response)
        except error.HTTPError as exc:
            status, value = exc.code, json.load(exc)
        assert status == expected, (route, status, value)
        if expected == 200 and route != 'authenticate':
            assert value['nonce'] == body['nonce']
        return value

    try:
        yield db, worker, post
    finally:
        api.shutdown()
        api.server_close()
        thread.join(timeout=5)


def test_interrupted_update_checkpoint_recovery_and_resume(staging):
    db, worker, post = staging
    assert post('ready', model_sha256=MODEL_SHA)['state'] == 'ready'
    job = dict(version=3, job_id='job_' + secrets.token_hex(12), fen=chess.STARTING_FEN,
               model_sha256=MODEL_SHA, legal_moves=sorted(m.uci() for m in chess.Board().legal_moves),
               max_tokens=32, timeout_seconds=600, lease_token=secrets.token_hex(32))
    WorkerInferenceDelivery(db).assign_from_arena(worker, job)
    assert post('job-poll')['job'] == job
    original = tournament_checkpoint.load(db, SANDBOX_TOURNAMENT_ID)
    pause = WorkerUpdatePause(db)
    rid = pause.request(worker, job['job_id'])
    instruction = post('update-pause-poll')['instruction']
    digest = hashlib.sha256(job['lease_token'].encode()).hexdigest()
    assert instruction['request_id'] == rid and instruction['lease_token_sha256'] == digest
    result = dict(version=2, job_id=job['job_id'], uci='e2e4', lease_token=job['lease_token'])
    post('job-result', 409, result=result)
    with pytest.raises(ValueError, match='native STOP report'):
        pause.acknowledge(worker, job['job_id'], rid, worker_stopped=True,
                          checkpoint_verified=True, lease_token_sha256=digest)
    report = dict(job_id=job['job_id'], request_id=rid, lease_token=job['lease_token'])
    assert post('native-stop-report', **report)['install_allowed'] is False
    assert post('native-stop-report', **report)['state'] == 'stop_report_received'
    assert pause.pending(worker)['phase'] == 'requested'
    # Trusted native engine evidence is simulated; HTTP report alone cannot ACK.
    pause.acknowledge(worker, job['job_id'], rid, worker_stopped=True,
                      checkpoint_verified=True, lease_token_sha256=digest)
    checkpoint = dict(schema_version=1, job_id=job['job_id'], resume_mode='restart',
                      payload={'fen': original['fen'], 'moves': original['moves']})
    verify = lambda c: c == checkpoint and tournament_checkpoint.load(db, SANDBOX_TOURNAMENT_ID) == original
    hold = secrets.token_hex(32)
    args = dict(hold_token=hold, checkpoint=checkpoint, worker_stopped_verified=True,
                checkpoint_verifier=verify)
    # Force a real SQLite transaction failure after checkpoint INSERT.
    with sqlite3.connect(db) as con:
        con.execute("CREATE TRIGGER stage_fail_hold BEFORE INSERT ON worker_update_holds BEGIN SELECT RAISE(ABORT, 'stage rollback'); END")
    with pytest.raises(sqlite3.IntegrityError, match='stage rollback'):
        pause.finalize_verified_pause(worker, job['job_id'], rid, **args)
    assert pause.read_resume_checkpoint(worker) is None
    assert pause.jobs.state(worker) == 'running'
    with sqlite3.connect(db) as con:
        assert con.execute('SELECT count(*) FROM worker_update_holds').fetchone() == (0,)
        con.execute('DROP TRIGGER stage_fail_hold')
    saved = pause.finalize_verified_pause(worker, job['job_id'], rid, **args)
    pause = WorkerUpdatePause(db)  # reopen coordinator after simulated restart
    assert pause.read_resume_checkpoint(worker)['sha256'] == saved['sha256']
    assert post('update-hold-status', hold_token=hold)['install_allowed'] is False
    assert post('update-resume-status')['state'] == 'waiting_update_reconciliation'
    assert post('job-poll')['state'] == 'blocked'
    post('update-release', 409, hold_token=secrets.token_hex(32))
    assert post('update-release', hold_token=hold)['released'] is True
    assert post('update-resume-status')['state'] == 'awaiting_local_engine'
    assert post('update-resume-status')['resume_available'] is False
    def receipt(offer):
        assert offer['checkpoint'] == checkpoint
        return {k: offer[k] for k in ('version','worker_id','job_id','request_id','checkpoint_sha256','resume_mode','nonce')} | {'authenticated': True, 'engine_accepted_verified': True}
    resume = dict(installed_identity_verified=True, stopped_engine_verified=True, checkpoint_verifier=verify)
    with pytest.raises(ValueError, match='mismatched'):
        pause.dispatch_trusted_resume(worker, **resume, send_and_verify=lambda o: dict(receipt(o), nonce='0'*32))
    assert pause.read_resume_checkpoint(worker)['sha256'] == saved['sha256']
    assert pause.jobs.state(worker) == 'unknown'
    assert pause.dispatch_trusted_resume(worker, **resume, send_and_verify=receipt)['state'] == 'running'
    assert pause.read_resume_checkpoint(worker) is None
    assert pause.pending(worker) is None
    with pytest.raises(ValueError):
        pause.dispatch_trusted_resume(worker, **resume, send_and_verify=receipt)
    assert tournament_checkpoint.load(db, SANDBOX_TOURNAMENT_ID) == original


@pytest.mark.parametrize("failure", ["none", "rollback", "concurrent", "missing_report"])
def test_resume_does_not_reissue_pre_stop_lease(staging, failure):
    """Old capability must not authorize a result after a resumed execution."""
    db, worker, post = staging
    post('ready', model_sha256=MODEL_SHA)
    job = dict(version=3, job_id='job_' + secrets.token_hex(12), fen=chess.STARTING_FEN,
               model_sha256=MODEL_SHA, legal_moves=sorted(m.uci() for m in chess.Board().legal_moves),
               max_tokens=32, timeout_seconds=600, lease_token=secrets.token_hex(32))
    WorkerInferenceDelivery(db).assign_from_arena(worker, job)
    # Receipt already buffered before STOP must not survive rotation.
    assert post('job-result', result=dict(version=2, job_id=job['job_id'], uci='d2d4', lease_token=job['lease_token']))['state'] == 'received_not_applied'
    pause = WorkerUpdatePause(db)
    rid = pause.request(worker, job['job_id'])
    post('native-stop-report', job_id=job['job_id'], request_id=rid, lease_token=job['lease_token'])
    digest = hashlib.sha256(job['lease_token'].encode()).hexdigest()
    pause.acknowledge(worker, job['job_id'], rid, worker_stopped=True, checkpoint_verified=True, lease_token_sha256=digest)
    cp = dict(schema_version=1, job_id=job['job_id'], resume_mode='restart', payload={'fen': job['fen']})
    hold = secrets.token_hex(32)
    saved = pause.finalize_verified_pause(worker, job['job_id'], rid, hold_token=hold, checkpoint=cp,
                                         worker_stopped_verified=True, checkpoint_verifier=lambda c: c == cp)
    post('update-release', hold_token=hold)
    def reconcile():
        return WorkerUpdatePause(db).reconcile_resume(
            worker, job['job_id'], rid, checkpoint_sha256=saved['sha256'], timeout_seconds=60,
            worker_ready_verified=True, resume_accepted_verified=True, checkpoint_verifier=lambda c: c == cp)

    def snapshot():
        with sqlite3.connect(db) as con:
            return {table: con.execute(f'SELECT * FROM {table}').fetchall() for table in (
                'worker_inference_assignments', 'worker_inference_results', 'worker_native_stop_reports',
                'worker_job_safety', 'worker_update_resume', 'worker_update_pause_requests')}

    before = snapshot()
    if failure == 'rollback':
        with sqlite3.connect(db) as con:
            con.execute("CREATE TRIGGER stage_fail_resume BEFORE DELETE ON worker_update_resume BEGIN SELECT RAISE(ABORT, 'resume rollback'); END")
        with pytest.raises(sqlite3.IntegrityError, match='resume rollback'):
            reconcile()
        assert snapshot() == before
        assert post('job-poll')['state'] == 'blocked'
        with sqlite3.connect(db) as con:
            con.execute('DROP TRIGGER stage_fail_resume')
    elif failure == 'missing_report':
        with sqlite3.connect(db) as con:
            con.execute('DELETE FROM worker_native_stop_reports WHERE worker_id=?', (worker,))
        with pytest.raises(ValueError, match='native STOP report'):
            reconcile()
        assert post('job-poll')['state'] == 'blocked'
        assert pause.read_resume_checkpoint(worker)['sha256'] == saved['sha256']
        with sqlite3.connect(db) as con:
            con.executemany('INSERT INTO worker_native_stop_reports VALUES (?,?,?,?,?)', before['worker_native_stop_reports'])
    if failure == 'concurrent':
        from concurrent.futures import ThreadPoolExecutor
        barrier = threading.Barrier(2)
        def attempt():
            barrier.wait(timeout=5)
            try:
                return reconcile()['state']
            except ValueError as exc:
                assert 'already consumed' in str(exc)
                return 'rejected'
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(attempt) for _ in range(2)]
            assert sorted(f.result(timeout=10) for f in futures) == ['rejected', 'running']
    else:
        assert reconcile()['state'] == 'running'
    assert snapshot()['worker_native_stop_reports'] == []
    assert snapshot()['worker_inference_results'] == []
    assert pause.read_resume_checkpoint(worker) is None
    post('job-result', 409, result=dict(version=2, job_id=job['job_id'], uci='e2e4', lease_token=job['lease_token']))
    fresh = post('job-poll')['job']
    assert fresh['lease_token'] != job['lease_token']
    assert fresh['job_id'] == job['job_id'] and fresh['fen'] == job['fen']
    assert fresh['timeout_seconds'] == 60
    assert post('job-result', result=dict(version=2, job_id=job['job_id'], uci='e2e4', lease_token=fresh['lease_token']))['state'] == 'received_not_applied'
