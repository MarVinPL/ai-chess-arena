import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
import tournament_checkpoint
from remote_worker_job_binding import BoundWorkerResults
from remote_worker_tokens import WorkerTokenStore

ROOT = Path(__file__).resolve().parents[1]


def test_service_rejects_uninitialized_database(tmp_path):
    path = tmp_path / 'empty.sqlite'
    sqlite3.connect(path).close()
    proc = subprocess.run([sys.executable, str(ROOT / 'remote_worker_service.py'),
                           '--db', str(path), '--port', '19444'],
                          capture_output=True, text=True, timeout=5)
    assert proc.returncode != 0
    assert 'missing remote worker tables' in proc.stderr


def test_systemd_template_is_disabled_by_default():
    unit = (ROOT / 'deploy/remote-worker/ai-chess-remote-worker.service.example').read_text()
    assert '127.0.0.1' in (ROOT / 'remote_worker_http_ingest.py').read_text()
    assert 'ExecStart=' in unit
    assert 'NoNewPrivileges=true' in unit
    assert 'IPAddressDeny=any' in unit
    assert 'IPAddressAllow=localhost' in unit
    assert 'ProtectSystem=strict' in unit
    assert not (ROOT / 'deploy/remote-worker/ai-chess-remote-worker.service').exists()


def test_standalone_gateway_accepts_result_and_stops_on_sigterm(tmp_path):
    import http.client
    import json
    import socket
    import time
    from test_remote_worker_job_binding import setup_game

    path, jobs, _, digest = setup_game(tmp_path)
    token = WorkerTokenStore(path).issue('worker_12345678', ttl_seconds=600)
    jobs.assign_bound('worker_12345678', 'job_12345678', tournament_id=1,
                      pairing_id=7, expected_sha256=digest, timeout_seconds=90)
    with jobs._db() as db:
        db.execute('UPDATE worker_job_safety SET deadline=? WHERE worker_id=?',
                   (time.time() + 90, 'worker_12345678'))
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    process = subprocess.Popen([sys.executable, str(ROOT / 'remote_worker_service.py'),
                                '--db', str(path), '--port', str(port)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    def request(payload):
        conn = http.client.HTTPConnection('127.0.0.1', port, timeout=1)
        try:
            conn.request('POST', '/internal/remote-worker/result', json.dumps(payload),
                         {'Content-Type': 'application/json'})
            response = conn.getresponse()
            response.read()
            return response.status
        finally:
            conn.close()
    try:
        payload = {'worker_id': 'worker_12345678', 'token': token,
                   'result': {'version': 1, 'job_id': 'job_12345678', 'uci': 'e2e4'}}
        for _ in range(60):
            assert process.poll() is None, 'Gateway process exited unexpectedly'
            try:
                status = request({**payload, 'token': 'wrong'})
                break
            except (OSError, TimeoutError):
                time.sleep(0.05)
        else:
            pytest.fail('Gateway did not start listening')
        assert status == 401
        assert request(payload) == 202
        assert request(payload) == 401
        assert len(jobs.pending()) == 1
        process.terminate()
        assert process.wait(timeout=5) == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        process.stderr.close()
