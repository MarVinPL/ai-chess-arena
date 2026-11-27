"""Exercise a disposable gateway under the user's systemd manager on VPS."""
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest
from remote_worker_tokens import WorkerTokenStore
from test_remote_worker_job_binding import setup_game

ROOT = Path(__file__).resolve().parents[1]


def _systemctl(*args):
    return subprocess.run(['systemctl', '--user', *args], capture_output=True,
                          text=True, timeout=8)


@pytest.mark.skipif(not shutil.which('systemd-run'), reason='systemd unavailable')
def test_disposable_systemd_gateway_lifecycle(tmp_path):
    # Non-interactive runners may have systemd binaries but no user-session
    # manager/bus.  An exit code of 1 alone does not prove systemd is usable.
    if not os.environ.get('XDG_RUNTIME_DIR'):
        pytest.skip('No user systemd session (XDG_RUNTIME_DIR missing)')
    probe = _systemctl('is-system-running')
    bus_error = 'Failed to connect to bus' in probe.stderr or 'Failed to connect to user scope bus' in probe.stderr
    if bus_error or probe.returncode not in (0, 1):
        pytest.skip('User systemd manager unavailable')
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
    unit = 'arena-worker-test-' + uuid.uuid4().hex[:12]
    command = ['systemd-run', '--user', '--unit', unit,
               '--property=Restart=no', '--property=MemoryMax=512M',
               '--property=NoNewPrivileges=yes', '--property=IPAddressDeny=any',
               '--property=IPAddressAllow=localhost',
               sys.executable, str(ROOT / 'remote_worker_service.py'),
               '--db', str(path), '--port', str(port)]
    start = subprocess.run(command, capture_output=True, text=True, timeout=10)
    if start.returncode != 0:
        pytest.fail('Transient systemd unit failed: ' + start.stderr)
    try:
        import http.client
        for _ in range(50):
            if _systemctl('is-active', unit).stdout.strip() == 'failed':
                pytest.fail('Gateway unit failed during startup')
            try:
                conn = http.client.HTTPConnection('127.0.0.1', port, timeout=1)
                conn.request('POST', '/internal/remote-worker/result',
                             json.dumps({'worker_id': 'worker_12345678', 'token': token,
                                         'result': {'version': 1, 'job_id': 'job_12345678',
                                                    'uci': 'e2e4'}}),
                             {'Content-Type': 'application/json'})
                response = conn.getresponse()
                response.read()
                conn.close()
                assert response.status == 202
                break
            except (OSError, TimeoutError):
                time.sleep(0.1)
        else:
            pytest.fail('Gateway did not become available under systemd')
        assert len(jobs.pending()) == 1
    finally:
        _systemctl('stop', unit)
        _systemctl('reset-failed', unit)
    assert _systemctl('is-active', unit).stdout.strip() != 'active'
