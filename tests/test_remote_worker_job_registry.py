import pytest
from remote_worker_job_registry import WorkerJobRegistry


def test_unknown_until_coordinator_assigns_and_confirms_terminal(tmp_path):
    now = [100.0]
    db = tmp_path / 'jobs.sqlite'
    registry = WorkerJobRegistry(db, clock=lambda: now[0])
    worker, job = 'worker_12345678', 'job_12345678'
    assert registry.state(worker) == 'unknown'
    with pytest.raises(ValueError):
        registry.record_assignment(worker, job, deadline=99)
    # The coordinator must establish a trustworthy initial idle state before
    # assignment; no API currently performs this transition.
    with registry._connect() as conn:
        conn.execute("INSERT INTO remote_worker_job_state VALUES (?, NULL, 'idle_confirmed', NULL, ?)", (worker, now[0]))
    registry.record_assignment(worker, job, deadline=200)
    assert WorkerJobRegistry(db, clock=lambda: now[0]).state(worker) == 'running'
    with pytest.raises(ValueError):
        registry.record_assignment(worker, 'job_87654321', deadline=200)
    now[0] = 201
    assert WorkerJobRegistry(db, clock=lambda: now[0]).state(worker) == 'unknown'
    registry.record_terminal(worker, job)
    assert WorkerJobRegistry(db, clock=lambda: now[0]).state(worker) == 'idle_confirmed'
    with pytest.raises(ValueError):
        registry.record_terminal(worker, job)


def test_invalid_worker_and_no_implicit_idle(tmp_path):
    registry = WorkerJobRegistry(tmp_path / 'jobs.sqlite')
    with pytest.raises(ValueError):
        registry.state('bad')
    assert registry.state('worker_87654321') == 'unknown'
