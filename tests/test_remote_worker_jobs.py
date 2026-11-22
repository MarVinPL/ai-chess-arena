import pytest
from remote_worker_jobs import DurableWorkerJobs


def test_durable_lifecycle_and_fail_closed(tmp_path):
    path = tmp_path / 'jobs.db'
    now = [100]
    jobs = DurableWorkerJobs(path, clock=lambda: now[0])
    worker, job = 'worker_12345678', 'job_12345678'
    assert jobs.state(worker) == 'unknown'
    jobs.assign(worker, job, timeout_seconds=30)
    assert DurableWorkerJobs(path, clock=lambda: now[0]).state(worker) == 'running'
    with pytest.raises(ValueError):
        jobs.assign(worker, 'job_87654321', timeout_seconds=30)
    now[0] = 131
    assert jobs.state(worker) == 'unknown'
    with pytest.raises(ValueError):
        jobs.complete(worker, job)
    jobs.invalidate(worker)
    assert jobs.state(worker) == 'unknown'


def test_committed_terminal_state_persists(tmp_path):
    path = tmp_path / 'jobs.db'
    jobs = DurableWorkerJobs(path, clock=lambda: 100)
    jobs.assign('worker_12345678', 'job_12345678', timeout_seconds=30)
    jobs.complete('worker_12345678', 'job_12345678')
    assert DurableWorkerJobs(path, clock=lambda: 110).state('worker_12345678') == 'idle_confirmed'
    with pytest.raises(ValueError):
        jobs.complete('worker_12345678', 'job_12345678')
    jobs.invalidate('worker_12345678')
    assert jobs.state('worker_12345678') == 'unknown'
