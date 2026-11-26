import time
from remote_worker_recovery_report import inspect_unresolved_jobs
from remote_worker_job_registry import WorkerJobRegistry
from remote_worker_tokens import WorkerTokenStore
from test_remote_worker_job_binding import setup_game


def test_report_shows_running_then_unknown_without_mutating(tmp_path):
    path, jobs, _, digest = setup_game(tmp_path)
    worker, job = 'worker_12345678', 'job_12345678'
    WorkerTokenStore(path).issue(worker, ttl_seconds=600)
    jobs.assign_bound(worker, job, tournament_id=1, pairing_id=7,
                      expected_sha256=digest, timeout_seconds=90)
    with jobs._db() as db:
        db.execute('UPDATE worker_job_safety SET deadline=? WHERE worker_id=?',
                   (time.time() + 90, worker))
    first = inspect_unresolved_jobs(path)
    assert len(first) == 1
    assert first[0]['action'] == 'wait_for_worker'
    assert first[0]['checkpoint_matches'] is True
    assert inspect_unresolved_jobs(path) == first
    assert jobs.state(worker) == 'running'
    WorkerJobRegistry(path).recover_after_restart()
    second = inspect_unresolved_jobs(path)
    assert second[0]['action'] == 'manual_reconciliation'
    assert jobs.state(worker) == 'unknown'


def test_report_detects_stale_checkpoint_without_writing(tmp_path):
    path, jobs, _, digest = setup_game(tmp_path)
    jobs.assign_bound('worker_12345678', 'job_12345678', tournament_id=1,
                      pairing_id=7, expected_sha256=digest, timeout_seconds=90)
    with jobs._db() as db:
        db.execute('UPDATE tournament_checkpoints SET sha256=? WHERE tournament_id=1', ('0' * 64,))
    result = inspect_unresolved_jobs(path)
    assert result[0]['checkpoint_matches'] is False
    assert result[0]['action'] == 'manual_checkpoint_conflict'
