import pytest
from remote_worker_result_journal import WorkerResultJournal
from remote_worker_jobs import DurableWorkerJobs


def test_atomic_staging_survives_restart_and_retries(tmp_path):
    path = tmp_path / 'jobs.sqlite'
    journal = WorkerResultJournal(path, clock=lambda: 100)
    worker, job = 'worker_12345678', 'job_12345678'
    journal.assign(worker, job, timeout_seconds=90)
    result = {'job_id': job, 'uci': 'e2e4'}
    assert journal.stage_validated(worker, job, result)
    assert not journal.stage_validated(worker, job, result)
    with pytest.raises(ValueError, match='Conflicting'):
        journal.stage_validated(worker, job, {'job_id': job, 'uci': 'e2e3'})
    DurableWorkerJobs(path, clock=lambda: 110).recover_after_restart()
    assert WorkerResultJournal(path, clock=lambda: 110).pending() == [(job, worker, result)]
    assert journal.state(worker) == 'unknown'


def test_invalid_or_expired_result_never_stages(tmp_path):
    path = tmp_path / 'jobs.sqlite'
    now = [100]
    journal = WorkerResultJournal(path, clock=lambda: now[0])
    worker, job = 'worker_12345678', 'job_12345678'
    journal.assign(worker, job, timeout_seconds=10)
    with pytest.raises(ValueError):
        journal.stage_validated(worker, job, {'job_id': 'job_87654321', 'uci': 'e2e4'})
    now[0] = 111
    with pytest.raises(ValueError):
        journal.stage_validated(worker, job, {'job_id': job, 'uci': 'e2e4'})
    assert journal.pending() == []
    assert journal.state(worker) == 'unknown'
