"""Isolated Caddy TLS -> loopback HTTP -> SQLite smoke test on the VPS."""
import http.client
import json
import os
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest
from remote_worker_http_ingest import create_loopback_server
from remote_worker_tokens import WorkerTokenStore
from test_remote_worker_job_binding import setup_game


def _free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


@pytest.mark.skipif(not shutil.which('caddy') or not shutil.which('curl'), reason='Caddy/curl unavailable')
def test_https_caddy_to_authenticated_result_journal(tmp_path):
    path, jobs, _, digest = setup_game(tmp_path)
    token = WorkerTokenStore(path).issue('worker_12345678', ttl_seconds=600)
    jobs.assign_bound('worker_12345678', 'job_12345678', tournament_id=1,
                      pairing_id=7, expected_sha256=digest, timeout_seconds=90)
    with jobs._db() as db:
        db.execute('UPDATE worker_job_safety SET deadline=? WHERE worker_id=?',
                   (time.time() + 90, 'worker_12345678'))
    backend = create_loopback_server(path)
    backend_thread = threading.Thread(target=backend.serve_forever, daemon=True)
    backend_thread.start()
    https_port = _free_port()
    config = tmp_path / 'Caddyfile'
    config.write_text('''{
    admin off
    auto_https disable_redirects
}
https://127.0.0.1:%d {
    bind 127.0.0.1
    tls internal
    @result {
        method POST
        path /internal/remote-worker/result
    }
    handle @result {
        request_body {
            max_size 2048
        }
        reverse_proxy 127.0.0.1:%d
    }
    handle {
        respond 404
    }
}
''' % (https_port, backend.server_port))
    env = {**os.environ, 'XDG_DATA_HOME': str(tmp_path / 'data'),
           'XDG_CONFIG_HOME': str(tmp_path / 'config')}
    caddy = subprocess.Popen(['caddy', 'run', '--config', str(config), '--adapter', 'caddyfile'],
                             env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def request(data, path='/internal/remote-worker/result'):
        return subprocess.run(['curl', '--noproxy', '*', '-ksS', '-o', '/dev/null',
                               '-w', '%{http_code}', '--max-time', '3',
                               '-H', 'Content-Type: application/json', '--data-binary', '@-',
                               'https://127.0.0.1:%d%s' % (https_port, path)],
                              input=data, text=True, capture_output=True, timeout=5)

    try:
        for _ in range(50):
            if caddy.poll() is not None:
                pytest.fail('Isolated Caddy exited before TLS startup')
            probe = request('{}', '/not-found')
            if probe.returncode == 0 and probe.stdout == '404':
                break
            time.sleep(0.1)
        else:
            pytest.fail('Isolated Caddy TLS listener did not start')
        payload = json.dumps({'worker_id': 'worker_12345678', 'token': token,
                              'result': {'version': 1, 'job_id': 'job_12345678', 'uci': 'e2e4'}})
        assert request(json.dumps({'worker_id': 'worker_12345678', 'token': 'wrong',
                                   'result': {'version': 1, 'job_id': 'job_12345678', 'uci': 'e2e4'}})).stdout == '401'
        assert request(payload).stdout == '202'
        assert request(payload).stdout == '401'
        assert len(jobs.pending()) == 1
    finally:
        caddy.terminate()
        try:
            caddy.wait(timeout=5)
        except subprocess.TimeoutExpired:
            caddy.kill()
            caddy.wait(timeout=5)
        backend.shutdown()
        backend.server_close()
        backend_thread.join(timeout=3)
