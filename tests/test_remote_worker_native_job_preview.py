"""Read-only native worker assignment previews require fully bounded jobs.

Tests are offline and never install, download or run an AI model.
"""
import json
import sqlite3
import time
from threading import Thread
from urllib import request, error
from pathlib import Path

import pytest

from remote_worker_job_binding import BoundWorkerResults
from remote_worker_job_delivery import AuthenticatedJobDelivery
from remote_worker_http_ingest import create_loopback_server
from remote_worker_tokens import WorkerTokenStore
from test_remote_worker_job_binding import setup_game

WORKER = 'android_marvin_01'
JOB = 'job_12345678'
MODEL = 'a' * 64


def prepare(tmp_path, *, manifest):
    path, _, old, digest = setup_game(tmp_path)
    jobs = BoundWorkerResults(path)
    token = WorkerTokenStore(path).issue(WORKER, ttl_seconds=600)
    params = {'model_sha256': MODEL, 'max_tokens': 128} if manifest else {}
    jobs.assign_bound(WORKER, JOB, tournament_id=1, pairing_id=7,
                      expected_sha256=digest, timeout_seconds=300, **params)
    return path, jobs, token, old


def test_incomplete_jobs_are_never_exposed_as_inference(tmp_path):
    path, jobs, token, old = prepare(tmp_path, manifest=False)
    delivery = AuthenticatedJobDelivery(path)
    assert delivery.retrieve(WORKER, token) == {
        'version': 1, 'job_id': JOB, 'fen': old['fen']
    }
    assert delivery.retrieve(WORKER, token, require_inference_manifest=True) is None
    with jobs._db() as db:
        assert db.execute('SELECT COUNT(*) FROM remote_inference_manifest').fetchone()[0] == 0


def test_atomic_inference_job_exposes_only_model_digest_and_bounds(tmp_path):
    path, jobs, token, old = prepare(tmp_path, manifest=True)
    expected = {'version': 1, 'job_id': JOB, 'fen': old['fen'],
                'model_sha256': MODEL, 'max_tokens': 128, 'timeout_seconds': 300}
    assert AuthenticatedJobDelivery(path).retrieve(
        WORKER, token, require_inference_manifest=True) == expected
    with jobs._db() as db:
        assert db.execute('SELECT model_sha256, max_tokens, timeout_seconds '
                          'FROM remote_inference_manifest WHERE job_id=?',
                          (JOB,)).fetchone() == (MODEL, 128, 300)
    assert 'token' not in expected
    assert 'path' not in expected
    assert 'command' not in expected


@pytest.mark.parametrize('model,tokens', [
    (None, 128), (MODEL, None), ('A'*64, 128),
    ('../model.gguf', 128), (MODEL, 0), (MODEL, 1025),
    (MODEL, True),
])
def test_invalid_model_manifest_leaves_no_reserved_job(tmp_path, model, tokens):
    path, _, _, digest = setup_game(tmp_path)
    jobs = BoundWorkerResults(path)
    with pytest.raises(ValueError):
        jobs.assign_bound(WORKER, JOB, tournament_id=1, pairing_id=7,
                          expected_sha256=digest, timeout_seconds=300,
                          model_sha256=model, max_tokens=tokens)
    with jobs._db() as db:
        assert not db.execute('SELECT 1 FROM remote_job_binding').fetchone()
        assert not db.execute('SELECT 1 FROM remote_inference_manifest').fetchone()
        assert not db.execute('SELECT 1 FROM worker_job_safety').fetchone()


def test_native_preview_pull_over_loopback_and_revocation(tmp_path):
    path, jobs, token, old = prepare(tmp_path, manifest=True)
    server = create_loopback_server(path)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def post(body):
        req = request.Request(
            f'http://127.0.0.1:{server.server_port}/internal/remote-worker/inference-job',
            json.dumps(body).encode(), {'Content-Type': 'application/json'},
            method='POST')
        try:
            with request.urlopen(req, timeout=3) as response:
                return response.status, json.loads(response.read())
        except error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    try:
        valid = {'worker_id': WORKER, 'token': token}
        status, data = post(valid)
        assert status == 200
        assert data['job']['model_sha256'] == MODEL
        assert data['job']['fen'] == old['fen']
        assert data['job']['timeout_seconds'] == 300
        assert data['job']['max_tokens'] == 128
        assert data['job']['job_id'] == JOB
        assert post({**valid, 'worker_id': 'other_worker_01'})[0] == 401
        assert post({**valid, 'token': 'incorrect-token'})[0] == 401
        assert post({**valid, 'unexpected': 'exec'})[0] == 401
        WorkerTokenStore(path).revoke(WORKER)
        assert post(valid)[0] == 401
        assert len(jobs.pending()) == 0
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_public_route_is_read_only_with_bounded_json_and_server_token_checks():
    server = (Path(__file__).resolve().parents[1] / 'server.py').read_text()
    assert "if path == '/api/remote-workers/job':" in server
    assert "require_inference_manifest=True" in server
    validator = (Path(__file__).resolve().parents[1] / 'remote_worker_http_validation.py').read_text()
    assert "'Transfer-Encoding'" in validator
    assert "_worker_rate_policy.admit_peer" in server
    assert "_worker_rate_policy.admit_authenticated" in server
    assert "set(payload) != {'worker_id', 'token'}" in server
    assert "'Content-Length'" in validator



def test_native_tauri_preview_is_explicit_and_cannot_execute_commands():
    root = Path(__file__).resolve().parents[1]
    rust = (root / 'remote_worker_gui/src-tauri/src/lib.rs').read_text()
    ui = (root / 'remote_worker_gui/public/index.html').read_text()
    assert 'async fn preview_worker_job(' in rust
    assert 'validate_pending_worker_job' in rust
    assert '#[serde(deny_unknown_fields)]' in rust
    assert 'api/remote-workers/job' in rust
    assert 'invoke_handler(tauri::generate_handler![' in rust
    assert 'preview_worker_job' in rust
    assert "invoke('preview_worker_job'" in ui
    assert 'id="previewJob" type="button" class="secondary" disabled' in ui
    assert 'document.getElementById(\'previewJob\').disabled=!ok' in ui
    assert 'verifiedJobPreviewConnection=ok?{server,workerId}:null' in ui
    assert 'const job=await invoke(\'preview_worker_job\',connection)' in ui
    assert "invoke('start')" not in ui
    assert "invoke('submit_worker_result')" not in ui
    assert 'Command::new' not in rust
    assert 'localStorage.setItem(token' not in ui


def test_two_model_bound_remote_jobs_commit_two_moves_offline(tmp_path):
    """Two HTTPS-ready task manifests and result commits with a fake executor.

    This is NOT evidence of inference on an Android phone or of local GGUF
    loading: UCI results below are produced by the test itself.
    """
    import chess
    import tournament_checkpoint

    path, _, _, _ = setup_game(tmp_path)
    jobs = BoundWorkerResults(path)
    token = WorkerTokenStore(path).issue(WORKER, ttl_seconds=600)
    server = create_loopback_server(path)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def post(endpoint, data):
        req = request.Request(
            f'http://127.0.0.1:{server.server_port}/internal/remote-worker/{endpoint}',
            json.dumps(data).encode(), {'Content-Type': 'application/json'},
            method='POST')
        with request.urlopen(req, timeout=3) as response:
            return response.status, json.loads(response.read())

    try:
        from remote_worker_protocol import validate_job
        for ply, move_uci in enumerate(('e2e4', 'e7e5'), start=1):
            previous = tournament_checkpoint.load(path, 1)
            with jobs._db() as db:
                digest = db.execute(
                    'SELECT sha256 FROM tournament_checkpoints WHERE tournament_id=1'
                ).fetchone()[0]
            job_id = f'job_1234567{ply}'
            jobs.assign_bound(
                WORKER, job_id, tournament_id=1, pairing_id=7,
                expected_sha256=digest, timeout_seconds=300,
                model_sha256=MODEL, max_tokens=64,
            )
            status, delivered = post('inference-job', {'worker_id': WORKER, 'token': token})
            assert status == 200
            actual_job = delivered['job']
            assert validate_job(actual_job) == actual_job
            assert actual_job['job_id'] == job_id
            assert actual_job['model_sha256'] == MODEL
            assert actual_job['fen'] == previous['fen']
            result = {'version': 1, 'job_id': job_id, 'uci': move_uci}
            assert post('result', {'worker_id': WORKER, 'token': token, 'result': result})[0] == 202
            assert post('inference-job', {'worker_id': WORKER, 'token': token}) == (200, {'job': None})

            board = chess.Board(previous['fen'])
            board.push_uci(move_uci)
            record = {'uci': move_uci, 'ply': ply,
                      'color': 'white' if ply % 2 else 'black',
                      'fen': board.fen(), 'seconds': 0.1}
            updated = {**previous,
                       'moves': previous['moves'] + [record],
                       'move_times': previous['move_times'] + [0.1],
                       'fen': board.fen(),
                       'side_to_move': 'white' if board.turn else 'black'}
            assert jobs.apply_bound(job_id, updated) is True
            assert jobs.apply_bound(job_id, updated) is False

        final = tournament_checkpoint.load(path, 1)
        assert [record['uci'] for record in final['moves']] == ['e2e4', 'e7e5']
        assert not jobs.pending()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
