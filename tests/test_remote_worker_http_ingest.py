import http.client
import json
import threading
from remote_worker_http_ingest import RateLimiter, create_loopback_server
from test_remote_worker_job_binding import setup_game
from remote_worker_tokens import WorkerTokenStore


def test_loopback_http_ingest_limits_and_authenticated_result(tmp_path):
    path, journal, old, digest = setup_game(tmp_path)
    # HTTP adapter uses real time, so issue a real-time-valid token.
    token = WorkerTokenStore(path).issue('worker_12345678', ttl_seconds=600)
    journal.assign_bound('worker_12345678', 'job_12345678', tournament_id=1,
                         pairing_id=7, expected_sha256=digest, timeout_seconds=90)
    # Align lease clock with HTTP adapter's real clock for this test.
    with journal._db() as db:
        import time
        db.execute('UPDATE worker_job_safety SET deadline=? WHERE worker_id=?',
                   (time.time() + 90, 'worker_12345678'))
    server = create_loopback_server(path, limiter=RateLimiter(limit=4))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    def send(data, *, path='/internal/remote-worker/result', content_type='application/json'):
        conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
        conn.request('POST', path, data, {'Content-Type': content_type})
        response = conn.getresponse()
        status = response.status
        response.read()
        conn.close()
        return status
    try:
        payload = {'worker_id': 'worker_12345678', 'token': token,
                   'result': {'version': 1, 'job_id': 'job_12345678', 'uci': 'e2e4'}}
        assert send(json.dumps(payload), path='/wrong') == 404
        assert send('x' * 2049) == 413
        assert send(json.dumps({**payload, 'token': 'wrong'})) == 401
        assert send(json.dumps(payload)) == 202
        assert send(json.dumps(payload)) == 401
        assert send(json.dumps(payload)) == 429
        assert len(journal.pending()) == 1
        assert server.server_address[0] == '127.0.0.1'
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_rate_limiter_bounds_unique_peer_state_and_reclaims_expired():
    current = [100]
    limiter = RateLimiter(limit=2, window=10, clock=lambda: current[0], max_peers=2)
    assert limiter.allow('a')
    assert limiter.allow('b')
    assert not limiter.allow('c')
    assert limiter.allow('a')
    assert not limiter.allow('a')
    current[0] = 111
    assert limiter.allow('c')
    assert len(limiter.events) == 1
