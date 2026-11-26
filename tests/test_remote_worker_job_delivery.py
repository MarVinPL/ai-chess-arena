import json
import sqlite3
import time
from urllib import request, error
from threading import Thread

from remote_worker_job_delivery import AuthenticatedJobDelivery
from remote_worker_http_ingest import create_loopback_server
from remote_worker_tokens import WorkerTokenStore
from test_remote_worker_job_binding import setup_game


def test_job_pull_is_authenticated_and_does_not_assign(tmp_path):
    path, jobs, _, digest = setup_game(tmp_path)
    token = WorkerTokenStore(path).issue('worker_12345678', ttl_seconds=600)
    delivery = AuthenticatedJobDelivery(path)
    assert delivery.retrieve('worker_12345678', token) is None
    jobs.assign_bound('worker_12345678', 'job_12345678', tournament_id=1,
                      pairing_id=7, expected_sha256=digest, timeout_seconds=90)
    with jobs._db() as db:
        db.execute('UPDATE worker_job_safety SET deadline=? WHERE worker_id=?',
                   (time.time() + 90, 'worker_12345678'))
    assert delivery.retrieve('worker_12345678', token)['job_id'] == 'job_12345678'
    try:
        delivery.retrieve('worker_12345678', 'invalid')
        assert False, 'Expected invalid credentials'
    except ValueError:
        pass
    WorkerTokenStore(path).revoke('worker_12345678')
    try:
        delivery.retrieve('worker_12345678', token)
        assert False, 'Expected revoked credentials'
    except ValueError:
        pass


def test_job_pull_http_never_exposes_foreign_job(tmp_path):
    path, jobs, _, digest = setup_game(tmp_path)
    tokens = WorkerTokenStore(path)
    token = tokens.issue('worker_12345678', ttl_seconds=600)
    other = tokens.issue('worker_87654321', ttl_seconds=600)
    jobs.assign_bound('worker_12345678', 'job_12345678', tournament_id=1,
                      pairing_id=7, expected_sha256=digest, timeout_seconds=90)
    with jobs._db() as db:
        db.execute('UPDATE worker_job_safety SET deadline=? WHERE worker_id=?',
                   (time.time() + 90, 'worker_12345678'))
    server = create_loopback_server(path)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    def post(worker, credential):
        req = request.Request(f'http://127.0.0.1:{server.server_port}/internal/remote-worker/job',
                              json.dumps({'worker_id': worker, 'token': credential}).encode(),
                              {'Content-Type': 'application/json'}, method='POST')
        try:
            with request.urlopen(req, timeout=3) as response:
                return response.status, json.load(response)
        except error.HTTPError as exc:
            return exc.code, None
    try:
        assert post('worker_12345678', token)[1]['job']['job_id'] == 'job_12345678'
        assert post('worker_87654321', other) == (200, {'job': None})
        assert post('worker_87654321', token)[0] == 401
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
