import pytest
from remote_worker_heartbeat import WorkerHeartbeatStore
from remote_worker_tokens import WorkerTokenStore
from test_remote_worker_job_binding import setup_game


def test_authenticated_heartbeat_freshness_and_revocation(tmp_path):
    path, _, _, _ = setup_game(tmp_path)
    now = [1000.0]
    worker = 'worker_12345678'
    tokens = WorkerTokenStore(path, clock=lambda: now[0])
    token = tokens.issue(worker, ttl_seconds=600)
    store = WorkerHeartbeatStore(path, clock=lambda: now[0])
    assert not store.recent(worker)
    with pytest.raises(ValueError):
        store.record(worker, 'invalid')
    store.record(worker, token)
    assert store.recent(worker)
    now[0] += 31
    assert not store.recent(worker)
    store.record(worker, token)
    assert store.recent(worker)
    tokens.revoke(worker)
    with pytest.raises(ValueError):
        store.record(worker, token)


def test_heartbeat_never_confirms_idle_or_recovers_unknown_job(tmp_path):
    path, jobs, _, _ = setup_game(tmp_path)
    worker = 'worker_12345678'
    token = WorkerTokenStore(path).issue(worker, ttl_seconds=600)
    store = WorkerHeartbeatStore(path)
    store.record(worker, token)
    assert store.recent(worker)
    assert jobs.state(worker) == 'unknown'
