import pytest
from remote_worker_readiness import assert_worker_eligible, WorkerNotReady
from remote_worker_tokens import WorkerTokenStore
from test_remote_worker_job_binding import setup_game


def test_worker_readiness_requires_fresh_liveness_and_idle(tmp_path):
    path, jobs, _, digest = setup_game(tmp_path)
    worker = 'worker_12345678'
    tokens = WorkerTokenStore(path)
    tokens.issue(worker, ttl_seconds=600)
    with pytest.raises(WorkerNotReady, match='liveness'):
        assert_worker_eligible(path, worker)
    with pytest.raises(WorkerNotReady, match='idle'):
        assert_worker_eligible(path, worker, liveness_verified=True)
    with jobs._db() as db:
        db.execute("INSERT INTO worker_job_safety VALUES (?,NULL,'idle_confirmed',NULL,0)", (worker,))
    assert assert_worker_eligible(path, worker, liveness_verified=True)
    jobs.assign_bound(worker, 'job_12345678', tournament_id=1, pairing_id=7,
                      expected_sha256=digest, timeout_seconds=90)
    with pytest.raises(WorkerNotReady, match='idle'):
        assert_worker_eligible(path, worker, liveness_verified=True)


def test_worker_readiness_rejects_revoked_token_and_pending_result(tmp_path):
    path, jobs, _, _ = setup_game(tmp_path)
    worker = 'worker_12345678'
    tokens = WorkerTokenStore(path)
    tokens.issue(worker, ttl_seconds=600)
    with jobs._db() as db:
        db.execute("INSERT INTO worker_job_safety VALUES (?,NULL,'idle_confirmed',NULL,0)", (worker,))
        db.execute("INSERT INTO worker_result_journal VALUES (?, ?, ?, 'pending_application', 0)",
                   ('job_12345678', worker, '{}'))
    with pytest.raises(WorkerNotReady, match='unapplied'):
        assert_worker_eligible(path, worker, liveness_verified=True)
    with jobs._db() as db:
        db.execute('DELETE FROM worker_result_journal')
    tokens.revoke(worker)
    with pytest.raises(WorkerNotReady, match='credential'):
        assert_worker_eligible(path, worker, liveness_verified=True)
