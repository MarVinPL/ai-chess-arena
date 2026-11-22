"""Pause is not idle: request, ACK, restart and races stay fail-closed."""
import sqlite3

import pytest

from remote_worker_jobs import DurableWorkerJobs
from remote_worker_update_pause import WorkerUpdatePause

WORKER = "worker_12345678"
JOB = "job_12345678"


def running_registry(path, clock):
    jobs = DurableWorkerJobs(path, clock=lambda: clock[0])
    with sqlite3.connect(path) as db:
        db.execute(
            "INSERT INTO worker_job_safety VALUES (?,?,'running',?,?)",
            (WORKER, JOB, clock[0] + 300, clock[0]),
        )
    return jobs


def test_pause_request_survives_restart_and_never_fakes_idle(tmp_path):
    db = tmp_path / "pause.db"
    now = [100.]
    jobs = running_registry(db, now)
    pause = WorkerUpdatePause(db, clock=lambda: now[0])
    request_id = pause.request(WORKER, JOB)
    assert len(request_id) == 32
    assert pause.pending(WORKER) == {
        "job_id": JOB, "request_id": request_id, "phase": "requested"
    }
    recovered = WorkerUpdatePause(db, clock=lambda: now[0])
    assert recovered.pending(WORKER) == pause.pending(WORKER)
    assert jobs.state(WORKER) == "running"
    with pytest.raises(ValueError, match="pause"):
        jobs.reserve_update(WORKER)


def test_pause_ack_needs_stopped_worker_and_verified_checkpoint(tmp_path):
    db = tmp_path / "pause.db"
    now = [100.]
    jobs = running_registry(db, now)
    pause = WorkerUpdatePause(db, clock=lambda: now[0])
    req = pause.request(WORKER, JOB)
    for stopped, checkpoint in [(False, True), (True, False), (False, False)]:
        with pytest.raises(ValueError, match="verified checkpoint"):
            pause.acknowledge(WORKER, JOB, req, worker_stopped=stopped,
                              checkpoint_verified=checkpoint)
    with pytest.raises(ValueError, match="Stale"):
        pause.acknowledge(WORKER, "job_87654321", req, worker_stopped=True,
                          checkpoint_verified=True)
    assert pause.pending(WORKER)["phase"] == "requested"
    pause.acknowledge(WORKER, JOB, req, worker_stopped=True,
                      checkpoint_verified=True)
    assert pause.pending(WORKER)["phase"] == "acknowledged"
    assert jobs.state(WORKER) == "running"
    with pytest.raises(ValueError, match="pause"):
        jobs.reserve_update(WORKER)
    with pytest.raises(ValueError, match="Stale"):
        pause.acknowledge(WORKER, JOB, req, worker_stopped=True,
                          checkpoint_verified=True)


def test_outdated_or_duplicate_pause_is_rejected(tmp_path):
    db = tmp_path / "pause.db"
    now = [100.]
    jobs = running_registry(db, now)
    pause = WorkerUpdatePause(db, clock=lambda: now[0])
    with pytest.raises(ValueError, match="No active"):
        pause.request(WORKER, "job_87654321")
    req = pause.request(WORKER, JOB)
    with pytest.raises(ValueError, match="already"):
        pause.request(WORKER, JOB)
    with pytest.raises(ValueError, match="mismatch"):
        pause.clear(WORKER, "f" * 32 if req != "f" * 32 else "e" * 32)
    assert pause.pending(WORKER)["request_id"] == req
    now[0] += 400
    with pytest.raises(ValueError, match="Stale"):
        pause.acknowledge(WORKER, JOB, req, worker_stopped=True,
                          checkpoint_verified=True)
    assert jobs.state(WORKER) == "unknown"


def test_pause_cleanup_never_marks_worker_idle(tmp_path):
    db = tmp_path / "pause.db"
    now = [100.]
    jobs = running_registry(db, now)
    pause = WorkerUpdatePause(db, clock=lambda: now[0])
    req = pause.request(WORKER, JOB)
    pause.clear(WORKER, req)
    assert pause.pending(WORKER) is None
    assert jobs.state(WORKER) == "running"


def test_pending_pause_blocks_both_assignment_paths_and_idle_snapshot(tmp_path):
    from remote_worker_job_registry import WorkerJobRegistry

    db = tmp_path / "pause-scheduler.db"
    now = [100.]
    jobs = running_registry(db, now)
    pause = WorkerUpdatePause(db, clock=lambda: now[0])
    request_id = pause.request(WORKER, JOB)
    # The old task may complete concurrently. Its completion must not turn
    # the worker into an assignable resource while STOP/ACK is outstanding.
    jobs.complete(WORKER, JOB)
    assert jobs.state(WORKER) == "idle_confirmed"
    assert jobs.update_safety_snapshot(WORKER) == ("unknown", None)
    with pytest.raises(ValueError, match="pause"):
        jobs.assign(WORKER, "job_87654321", timeout_seconds=60)
    with pytest.raises(ValueError, match="pause"):
        WorkerJobRegistry(db, clock=lambda: now[0]).record_assignment(
            WORKER, "job_87654321", deadline=now[0] + 60
        )
    with pytest.raises(ValueError, match="pause"):
        jobs.reserve_update(WORKER)
    assert pause.pending(WORKER)["request_id"] == request_id
    # A controlled cleanup is separate from update authorization.
    pause.clear(WORKER, request_id)
    assert jobs.update_safety_snapshot(WORKER) == ("idle_confirmed", 0)
    jobs.assign(WORKER, "job_87654321", timeout_seconds=60)


def test_pending_pause_survives_restart_and_blocks_all_schedulers(tmp_path):
    from remote_worker_job_registry import WorkerJobRegistry

    db = tmp_path / "pause-restart.db"
    now = [100.]
    jobs = running_registry(db, now)
    pause = WorkerUpdatePause(db, clock=lambda: now[0])
    request_id = pause.request(WORKER, JOB)
    jobs.recover_after_restart()
    new_jobs = DurableWorkerJobs(db, clock=lambda: now[0])
    new_pause = WorkerUpdatePause(db, clock=lambda: now[0])
    assert new_pause.pending(WORKER)["request_id"] == request_id
    assert new_jobs.state(WORKER) == "unknown"
    with pytest.raises(ValueError, match="pause"):
        new_jobs.assign(WORKER, "job_87654321", timeout_seconds=60)
    with pytest.raises(ValueError, match="pause"):
        WorkerJobRegistry(db, clock=lambda: now[0]).record_assignment(
            WORKER, "job_87654321", deadline=now[0] + 60
        )
    assert new_jobs.update_safety_snapshot(WORKER) == ("unknown", None)


def test_pause_request_and_complete_assignment_race_remains_serialized(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    db = tmp_path / "pause-race.db"
    now = [100.]
    running_registry(db, now)
    request = WorkerUpdatePause(db, clock=lambda: now[0])

    def complete_and_assign():
        jobs = DurableWorkerJobs(db, clock=lambda: now[0])
        jobs.complete(WORKER, JOB)
        try:
            jobs.assign(WORKER, "job_87654321", timeout_seconds=60)
            return "assigned"
        except ValueError:
            return "blocked"

    def ask_pause():
        try:
            return request.request(WORKER, JOB)
        except ValueError:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        a = executor.submit(ask_pause)
        b = executor.submit(complete_and_assign)
        pause_id, outcome = a.result(), b.result()

    state = DurableWorkerJobs(db, clock=lambda: now[0])
    if pause_id is not None:
        assert outcome == "blocked"
        assert state.update_safety_snapshot(WORKER) == ("unknown", None)
    else:
        assert outcome == "assigned"
