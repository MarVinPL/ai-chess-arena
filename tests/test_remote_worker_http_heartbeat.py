"""Loopback-only heartbeat endpoint requires authenticated minimal JSON."""
import json
import threading
from urllib import request, error

from remote_worker_heartbeat import WorkerHeartbeatStore
from remote_worker_http_ingest import create_loopback_server
from remote_worker_tokens import WorkerTokenStore
from test_remote_worker_job_binding import setup_game


def test_authenticated_heartbeat_endpoint_never_changes_job_safety(tmp_path):
    path, jobs, _, _ = setup_game(tmp_path)
    worker = 'worker_12345678'
    token = WorkerTokenStore(path).issue(worker, ttl_seconds=600)
    server = create_loopback_server(path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    def send(payload):
        req = request.Request(f'http://127.0.0.1:{server.server_port}/internal/remote-worker/heartbeat',
                              json.dumps(payload).encode(),
                              {'Content-Type': 'application/json'}, method='POST')
        try:
            with request.urlopen(req, timeout=3) as response:
                return response.status
        except error.HTTPError as exc:
            return exc.code
    try:
        assert send({'worker_id': worker, 'token': 'invalid'}) == 401
        assert not WorkerHeartbeatStore(path).recent(worker)
        assert send({'worker_id': worker, 'token': token, 'idle': True}) == 401
        assert send({'worker_id': worker, 'token': token}) == 202
        assert WorkerHeartbeatStore(path).recent(worker)
        assert jobs.state(worker) == 'unknown'
        WorkerTokenStore(path).revoke(worker)
        assert send({'worker_id': worker, 'token': token}) == 401
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
