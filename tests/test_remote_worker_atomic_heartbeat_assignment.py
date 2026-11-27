"""Heartbeat-gated assignment validates presence inside the reservation transaction."""
from concurrent.futures import ThreadPoolExecutor
import pytest
from remote_worker_heartbeat import WorkerHeartbeatStore
from remote_worker_job_binding import BoundWorkerResults
from remote_worker_tokens import WorkerTokenStore
from test_remote_worker_job_binding import setup_game


def test_assignment_requires_heartbeat_and_active_credential(tmp_path):
    path, jobs, _, digest = setup_game(tmp_path)
    jobs = BoundWorkerResults(path)
    worker = 'worker_12345678'
    token_store = WorkerTokenStore(path)
    token = token_store.issue(worker, ttl_seconds=600)
    with jobs._db() as db:
        db.execute("INSERT INTO worker_job_safety VALUES (?,NULL,'idle_confirmed',NULL,0)", (worker,))
    kwargs = dict(tournament_id=1, pairing_id=7, expected_sha256=digest,
                  timeout_seconds=90, require_fresh_heartbeat=True)
    with pytest.raises(ValueError, match='heartbeat'):
        jobs.assign_bound(worker, 'job_12345678', **kwargs)
    WorkerHeartbeatStore(path).record(worker, token)
    assert jobs.assign_bound(worker, 'job_12345678', **kwargs)
    with pytest.raises(ValueError):
        jobs.assign_bound(worker, 'job_87654321', **kwargs)


def test_concurrent_reservation_only_one_wins(tmp_path):
    path, jobs, _, digest = setup_game(tmp_path)
    jobs = BoundWorkerResults(path)
    worker = 'worker_12345678'
    token = WorkerTokenStore(path).issue(worker, ttl_seconds=600)
    WorkerHeartbeatStore(path).record(worker, token)
    with jobs._db() as db:
        db.execute("INSERT INTO worker_job_safety VALUES (?,NULL,'idle_confirmed',NULL,0)", (worker,))
    def reserve(job):
        try:
            BoundWorkerResults(path).assign_bound(worker, job, tournament_id=1,
                pairing_id=7, expected_sha256=digest, timeout_seconds=90,
                require_fresh_heartbeat=True)
            return True
        except ValueError:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(reserve, ('job_12345678', 'job_87654321')))
    assert sorted(results) == [False, True]
    with jobs._db() as db:
        assert db.execute('SELECT count(*) FROM remote_job_binding').fetchone()[0] == 1
