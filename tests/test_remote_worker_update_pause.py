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


def _stopped_checkpoint():
    # The trusted coordinator verifier is deliberately separate from the
    # transport ACK and the SHA-256 storage integrity check.
    checkpoint = {
        "schema_version": 1, "job_id": JOB,
        "resume_mode": "resume", "payload": {"fen": "example", "moves": []}
    }
    verifier = lambda data: data["payload"] == {"fen": "example", "moves": []}
    return checkpoint, verifier


def test_atomic_verified_pause_persists_checkpoint_and_holds_assignment(tmp_path):
    db = tmp_path / "atomic-pause.db"
    now = [100.]
    jobs = running_registry(db, now)
    pause = WorkerUpdatePause(db, clock=lambda: now[0])
    request_id = pause.request(WORKER, JOB)
    pause.acknowledge(WORKER, JOB, request_id,
                      worker_stopped=True, checkpoint_verified=True)
    checkpoint, verifier = _stopped_checkpoint()
    hold = "a" * 64
    result = pause.finalize_verified_pause(
        WORKER, JOB, request_id, hold_token=hold, checkpoint=checkpoint,
        worker_stopped_verified=True, checkpoint_verifier=verifier,
    )
    assert result["job_id"] == JOB
    assert len(result["sha256"]) == 64
    assert jobs.state(WORKER) == "unknown"
    assert jobs.validate_update_hold(WORKER, hold)
    assert jobs.update_safety_snapshot(WORKER) == ("unknown", None)
    with pytest.raises(ValueError, match="reserved"):
        jobs.assign(WORKER, "job_87654321", timeout_seconds=60)
    recovered = WorkerUpdatePause(db, clock=lambda: now[0])
    assert recovered.read_resume_checkpoint(WORKER)["checkpoint"] == checkpoint
    with pytest.raises(ValueError, match="pending"):
        recovered.clear(WORKER, request_id)
    with pytest.raises(ValueError, match="reserved"):
        recovered.finalize_verified_pause(
            WORKER, JOB, request_id, hold_token="b" * 64,
            checkpoint=checkpoint, worker_stopped_verified=True,
            checkpoint_verifier=verifier,
        )
    jobs.release_update_hold(WORKER, hold)
    assert not jobs.validate_update_hold(WORKER, hold)
    assert recovered.read_resume_checkpoint(WORKER)["checkpoint"] == checkpoint
    with pytest.raises(ValueError, match="pause"):
        jobs.assign(WORKER, "job_87654321", timeout_seconds=60)


def test_verified_pause_requires_real_stopped_ack_and_trusted_checkpoint(tmp_path):
    db = tmp_path / "invalid-ack.db"
    now = [100.]
    jobs = running_registry(db, now)
    pause = WorkerUpdatePause(db, clock=lambda: now[0])
    request_id = pause.request(WORKER, JOB)
    checkpoint, verifier = _stopped_checkpoint()
    fields = dict(hold_token="a" * 64, checkpoint=checkpoint,
                  worker_stopped_verified=True, checkpoint_verifier=verifier)
    with pytest.raises(ValueError, match="ACK"):
        pause.finalize_verified_pause(WORKER, JOB, request_id, **fields)
    pause.acknowledge(WORKER, JOB, request_id,
                      worker_stopped=True, checkpoint_verified=True)
    with pytest.raises(ValueError, match="STOP"):
        pause.finalize_verified_pause(
            WORKER, JOB, request_id, **{**fields, "worker_stopped_verified": False})
    with pytest.raises(ValueError, match="verified"):
        pause.finalize_verified_pause(
            WORKER, JOB, request_id, **{**fields, "checkpoint_verifier": lambda _: False})
    with pytest.raises(ValueError, match="Invalid resume"):
        pause.finalize_verified_pause(
            WORKER, JOB, request_id, **{**fields, "checkpoint": {
                **checkpoint, "job_id": "job_wrong00",
            }})
    with pytest.raises(ValueError, match="Invalid client"):
        pause.finalize_verified_pause(
            WORKER, JOB, request_id, **{**fields, "hold_token": "a" * 63})
    assert jobs.state(WORKER) == "running"
    assert pause.read_resume_checkpoint(WORKER) is None
    assert pause.pending(WORKER)["phase"] == "acknowledged"
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT count(*) FROM worker_update_holds").fetchone()[0] == 0


def test_verified_pause_expired_lease_and_bad_checkpoint_are_atomic(tmp_path):
    db = tmp_path / "expiry.db"
    now = [100.]
    running_registry(db, now)
    pause = WorkerUpdatePause(db, clock=lambda: now[0])
    request_id = pause.request(WORKER, JOB)
    pause.acknowledge(WORKER, JOB, request_id,
                      worker_stopped=True, checkpoint_verified=True)
    checkpoint, verifier = _stopped_checkpoint()
    now[0] = 401.
    with pytest.raises(ValueError, match="lease"):
        pause.finalize_verified_pause(
            WORKER, JOB, request_id, hold_token="a" * 64,
            checkpoint=checkpoint, worker_stopped_verified=True,
            checkpoint_verifier=verifier,
        )
    assert pause.read_resume_checkpoint(WORKER) is None
    assert DurableWorkerJobs(db, clock=lambda: now[0]).state(WORKER) == "unknown"


def test_checkpoint_corruption_blocks_resume_and_does_not_delete_hold(tmp_path):
    db = tmp_path / "corrupt.db"
    now = [100.]
    jobs = running_registry(db, now)
    pause = WorkerUpdatePause(db, clock=lambda: now[0])
    request_id = pause.request(WORKER, JOB)
    pause.acknowledge(WORKER, JOB, request_id,
                      worker_stopped=True, checkpoint_verified=True)
    checkpoint, verifier = _stopped_checkpoint()
    hold = "f" * 64
    pause.finalize_verified_pause(
        WORKER, JOB, request_id, hold_token=hold, checkpoint=checkpoint,
        worker_stopped_verified=True, checkpoint_verifier=verifier,
    )
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE worker_update_resume SET checkpoint_json='{}' WHERE worker_id=?",
                     (WORKER,))
    with pytest.raises(ValueError, match="Corrupted"):
        WorkerUpdatePause(db, clock=lambda: now[0]).read_resume_checkpoint(WORKER)
    assert jobs.validate_update_hold(WORKER, hold)


def test_orphaned_resume_checkpoint_still_blocks_all_assignments(tmp_path):
    from remote_worker_job_registry import WorkerJobRegistry
    db = tmp_path / "orphan.db"
    now = [100.]
    jobs = running_registry(db, now)
    pause = WorkerUpdatePause(db, clock=lambda: now[0])
    request_id = pause.request(WORKER, JOB)
    pause.acknowledge(WORKER, JOB, request_id,
                      worker_stopped=True, checkpoint_verified=True)
    checkpoint, verifier = _stopped_checkpoint()
    token = "1" * 64
    pause.finalize_verified_pause(
        WORKER, JOB, request_id, hold_token=token, checkpoint=checkpoint,
        worker_stopped_verified=True, checkpoint_verifier=verifier
    )
    jobs.release_update_hold(WORKER, token)
    # Even if a stale administrator mistakenly removes the pause request,
    # a checkpointed interrupted job must not disappear from scheduling.
    with sqlite3.connect(db) as conn:
        conn.execute("DELETE FROM worker_update_pause_requests WHERE worker_id=?",
                     (WORKER,))
        conn.execute(
            "UPDATE worker_job_safety SET state='idle_confirmed',updated_at=? "
            "WHERE worker_id=?", (now[0], WORKER)
        )
    assert jobs.update_safety_snapshot(WORKER) == ("unknown", None)
    with pytest.raises(ValueError, match="resume checkpoint"):
        jobs.assign(WORKER, "job_87654321", timeout_seconds=60)
    with pytest.raises(ValueError, match="resume checkpoint"):
        WorkerJobRegistry(db, clock=lambda: now[0]).record_assignment(
            WORKER, "job_87654321", deadline=now[0] + 60
        )
    with pytest.raises(ValueError, match="resume checkpoint"):
        jobs.reserve_update(WORKER)
    assert pause.read_resume_checkpoint(WORKER)["checkpoint"] == checkpoint


def _prepared_resume(tmp_path):
    db = tmp_path / "resume.db"
    now = [100.]
    jobs = running_registry(db, now)
    pause = WorkerUpdatePause(db, clock=lambda: now[0])
    request_id = pause.request(WORKER, JOB)
    pause.acknowledge(WORKER, JOB, request_id,
                      worker_stopped=True, checkpoint_verified=True)
    checkpoint, verifier = _stopped_checkpoint()
    token = "2" * 64
    final = pause.finalize_verified_pause(
        WORKER, JOB, request_id, hold_token=token, checkpoint=checkpoint,
        worker_stopped_verified=True, checkpoint_verifier=verifier
    )
    return jobs, pause, now, request_id, token, final["sha256"], verifier


def test_reconcile_resume_rejects_active_hold_and_replays(tmp_path):
    jobs, pause, now, request_id, token, digest, verifier = _prepared_resume(tmp_path)
    params = dict(checkpoint_sha256=digest, timeout_seconds=180,
                  worker_ready_verified=True, resume_accepted_verified=True,
                  checkpoint_verifier=verifier)
    with pytest.raises(ValueError, match="reserved"):
        pause.reconcile_resume(WORKER, JOB, request_id, **params)
    assert pause.read_resume_checkpoint(WORKER) is not None
    jobs.release_update_hold(WORKER, token)
    result = pause.reconcile_resume(WORKER, JOB, request_id, **params)
    assert result == {"worker_id": WORKER, "job_id": JOB, "state": "running"}
    assert jobs.state(WORKER) == "running"
    assert pause.pending(WORKER) is None
    assert pause.read_resume_checkpoint(WORKER) is None
    with pytest.raises(ValueError, match="consumed"):
        pause.reconcile_resume(WORKER, JOB, request_id, **params)
    with pytest.raises(ValueError, match="confirmed idle"):
        jobs.assign(WORKER, "job_87654321", timeout_seconds=60)


def test_resume_ack_and_checkpoint_are_mandatory(tmp_path):
    jobs, pause, now, request_id, token, digest, verifier = _prepared_resume(tmp_path)
    jobs.release_update_hold(WORKER, token)
    params = dict(checkpoint_sha256=digest, timeout_seconds=180,
                  worker_ready_verified=True, resume_accepted_verified=True,
                  checkpoint_verifier=verifier)
    for change, pattern in [
        ({"worker_ready_verified": False}, "acceptance"),
        ({"resume_accepted_verified": False}, "acceptance"),
        ({"checkpoint_verifier": lambda _: False}, "rejected"),
        ({"checkpoint_sha256": "0" * 64}, "mismatch"),
        ({"timeout_seconds": 0}, "lease"),
    ]:
        with pytest.raises(ValueError, match=pattern):
            pause.reconcile_resume(WORKER, JOB, request_id, **(params | change))
    with pytest.raises(ValueError, match="mismatch"):
        pause.reconcile_resume(WORKER, "job_87654321", request_id, **params)
    assert jobs.state(WORKER) == "unknown"
    assert pause.read_resume_checkpoint(WORKER) is not None
    assert pause.pending(WORKER)["phase"] == "acknowledged"


def test_resume_integrity_and_restart_preserved_until_trusted_ack(tmp_path):
    jobs, pause, now, request_id, token, digest, verifier = _prepared_resume(tmp_path)
    jobs.release_update_hold(WORKER, token)
    recovered = WorkerUpdatePause(pause.path, clock=lambda: now[0])
    assert recovered.read_resume_checkpoint(WORKER)["sha256"] == digest
    with sqlite3.connect(pause.path) as db:
        db.execute(
            "UPDATE worker_update_resume SET checkpoint_json='{}' WHERE worker_id=?",
            (WORKER,)
        )
    with pytest.raises(ValueError, match="Corrupted"):
        recovered.reconcile_resume(
            WORKER, JOB, request_id, checkpoint_sha256=digest, timeout_seconds=180,
            worker_ready_verified=True, resume_accepted_verified=True,
            checkpoint_verifier=verifier
        )
    assert jobs.state(WORKER) == "unknown"
    assert recovered.pending(WORKER)["phase"] == "acknowledged"


def test_two_resume_dispatchers_cannot_consume_same_checkpoint(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    jobs, pause, now, request_id, token, digest, verifier = _prepared_resume(tmp_path)
    jobs.release_update_hold(WORKER, token)
    def dispatch(_):
        worker = WorkerUpdatePause(pause.path, clock=lambda: now[0])
        try:
            worker.reconcile_resume(
                WORKER, JOB, request_id, checkpoint_sha256=digest, timeout_seconds=180,
                worker_ready_verified=True, resume_accepted_verified=True,
                checkpoint_verifier=verifier
            )
            return "resumed"
        except ValueError:
            return "rejected"
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(dispatch, (1, 2)))
    assert sorted(results) == ["rejected", "resumed"]
    assert jobs.state(WORKER) == "running"
