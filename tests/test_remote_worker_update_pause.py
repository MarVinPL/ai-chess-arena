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
    with pytest.raises(ValueError, match="freshly confirmed"):
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
    with pytest.raises(ValueError, match="freshly confirmed"):
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
