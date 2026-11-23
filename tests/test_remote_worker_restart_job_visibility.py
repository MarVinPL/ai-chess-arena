"""Restart safety: unresolved remote jobs cannot be fetched or reassigned."""
import time
import pytest
from remote_worker_job_delivery import AuthenticatedJobDelivery
from remote_worker_job_registry import WorkerJobRegistry
from remote_worker_readiness import assert_worker_eligible, WorkerNotReady
from remote_worker_tokens import WorkerTokenStore
from test_remote_worker_job_binding import setup_game


def test_restart_invalidates_unfinished_job_and_requires_reconciliation(tmp_path):
    path, jobs, _, digest = setup_game(tmp_path)
    worker, job = 'worker_12345678', 'job_12345678'
    token = WorkerTokenStore(path).issue(worker, ttl_seconds=600)
    with jobs._db() as db:
        db.execute("INSERT INTO worker_job_safety VALUES (?,NULL,'idle_confirmed',NULL,0)", (worker,))
    jobs.assign_bound(worker, job, tournament_id=1, pairing_id=7,
                      expected_sha256=digest, timeout_seconds=90)
    with jobs._db() as db:
        db.execute('UPDATE worker_job_safety SET deadline=? WHERE worker_id=?',
                   (time.time() + 90, worker))
    delivery = AuthenticatedJobDelivery(path)
    assert delivery.retrieve(worker, token)['job_id'] == job
    WorkerJobRegistry(path).recover_after_restart()
    assert jobs.state(worker) == 'unknown'
    assert delivery.retrieve(worker, token) is None
    with pytest.raises(WorkerNotReady, match='idle'):
        assert_worker_eligible(path, worker, liveness_verified=True)
    with pytest.raises(ValueError):
        jobs.assign_bound(worker, 'job_87654321', tournament_id=1,
                          pairing_id=7, expected_sha256=digest, timeout_seconds=90)


def test_restart_invalidates_previously_idle_worker(tmp_path):
    path, jobs, _, _ = setup_game(tmp_path)
    worker = 'worker_12345678'
    WorkerTokenStore(path).issue(worker, ttl_seconds=600)
    with jobs._db() as db:
        db.execute("INSERT INTO worker_job_safety VALUES (?,NULL,'idle_confirmed',NULL,0)", (worker,))
    assert assert_worker_eligible(path, worker, liveness_verified=True)
    WorkerJobRegistry(path).recover_after_restart()
    with pytest.raises(WorkerNotReady, match='idle'):
        assert_worker_eligible(path, worker, liveness_verified=True)
