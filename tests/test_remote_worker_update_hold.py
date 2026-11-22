"""Coordinator update reservations: atomic with all assignment entrypoints."""
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from remote_worker_jobs import DurableWorkerJobs
from remote_worker_job_registry import WorkerJobRegistry

WORKER = "worker_12345678"


def initial_idle(db, now):
    jobs = DurableWorkerJobs(db, clock=lambda: now[0])
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO worker_job_safety VALUES (?,NULL,'idle_confirmed',NULL,?)",
            (WORKER, now[0]),
        )
    return jobs


def test_reservation_blocks_both_scheduler_apis_and_only_explicit_release_unlocks(tmp_path):
    db = tmp_path / "reservation.db"
    now = [100.0]
    jobs = initial_idle(db, now)
    token = jobs.reserve_update(WORKER)
    assert jobs.validate_update_hold(WORKER, token)
    assert jobs.update_safety_snapshot(WORKER) == ("unknown", None)
    with pytest.raises(ValueError, match="reserved"):
        jobs.assign(WORKER, "job_12345678", timeout_seconds=60)
    with pytest.raises(ValueError, match="reserved"):
        WorkerJobRegistry(db, clock=lambda: now[0]).record_assignment(
            WORKER, "job_12345678", deadline=now[0] + 60
        )
    assert jobs.state(WORKER) == "idle_confirmed"  # reservation, not mutated job status
    jobs.release_update_hold(WORKER, token)
    assert not jobs.validate_update_hold(WORKER, token)
    assert jobs.state(WORKER) == "unknown"
    # Clearing the reservation never claims the restarted Worker is idle.
    with pytest.raises(ValueError, match="confirmed idle"):
        jobs.assign(WORKER, "job_12345678", timeout_seconds=60)


def test_stale_idle_and_missing_registry_cannot_acquire_hold(tmp_path):
    db = tmp_path / "stale.db"
    now = [100.]
    jobs = DurableWorkerJobs(db, clock=lambda: now[0])
    with pytest.raises(ValueError, match="freshly"):
        jobs.reserve_update(WORKER)
    initial_idle(db, now)
    now[0] = 130.01
    with pytest.raises(ValueError, match="freshly"):
        jobs.reserve_update(WORKER)
    assert jobs.state(WORKER) == "idle_confirmed"
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT count(*) FROM worker_update_holds").fetchone()[0] == 0


def test_expired_reservation_remains_blocked_even_after_process_restart(tmp_path):
    db = tmp_path / "persistent.db"
    now = [100.]
    jobs = initial_idle(db, now)
    token = jobs.reserve_update(WORKER, ttl_seconds=30)
    now[0] = 131.
    recovered = DurableWorkerJobs(db, clock=lambda: now[0])
    assert not recovered.validate_update_hold(WORKER, token)
    with pytest.raises(ValueError, match="reserved"):
        recovered.assign(WORKER, "job_12345678", timeout_seconds=60)
    recovered.recover_after_restart()
    with pytest.raises(ValueError, match="reserved"):
        recovered.assign(WORKER, "job_12345678", timeout_seconds=60)
    # An expired install lease does not authorize automatic unlocking.
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT count(*) FROM worker_update_holds").fetchone()[0] == 1


def test_wrong_token_does_not_release_or_validate_hold(tmp_path):
    db = tmp_path / "wrong.db"
    now = [100.]
    jobs = initial_idle(db, now)
    token = jobs.reserve_update(WORKER)
    wrong = "0" * 64 if token != "0" * 64 else "1" * 64
    assert not jobs.validate_update_hold(WORKER, wrong)
    with pytest.raises(ValueError, match="Wrong"):
        jobs.release_update_hold(WORKER, wrong)
    with pytest.raises(ValueError, match="reserved"):
        jobs.reserve_update(WORKER)
    assert jobs.validate_update_hold(WORKER, token)


def test_reservation_assignment_race_is_serialized_across_connections(tmp_path):
    db = tmp_path / "race.db"
    now = [100.]
    initial_idle(db, now)
    def reserve(_):
        try:
            return "hold", DurableWorkerJobs(db, clock=lambda: now[0]).reserve_update(WORKER)
        except ValueError:
            return "rejected", None

    def assign(i):
        try:
            DurableWorkerJobs(db, clock=lambda: now[0]).assign(
                WORKER, f"job_{i:08d}", timeout_seconds=60)
            return "assignment", None
        except ValueError:
            return "rejected", None

    with ThreadPoolExecutor(max_workers=12) as executor:
        futures = [executor.submit(reserve, 0)] + [
            executor.submit(assign, i) for i in range(1, 12)]
        results = [future.result() for future in futures]
    successes = [(kind, token) for kind, token in results if kind != "rejected"]
    assert len(successes) == 1
    state = WorkerJobRegistry(db, clock=lambda: now[0]).state(WORKER)
    if successes[0][0] == "hold":
        assert state == "idle_confirmed"
        assert DurableWorkerJobs(db, clock=lambda: now[0]).validate_update_hold(
            WORKER, successes[0][1])
    else:
        assert state == "running"


def test_reservation_rejects_running_and_unknown_states(tmp_path):
    db = tmp_path / "running.db"
    now = [100.]
    jobs = initial_idle(db, now)
    jobs.assign(WORKER, "job_12345678", timeout_seconds=60)
    with pytest.raises(ValueError, match="freshly"):
        jobs.reserve_update(WORKER)
    jobs.invalidate(WORKER)
    with pytest.raises(ValueError, match="freshly"):
        jobs.reserve_update(WORKER)


def test_read_only_hold_inspection_distinguishes_active_expired_absent(tmp_path):
    db = tmp_path / "inspection.db"
    now = [100.]
    jobs = initial_idle(db, now)
    token = jobs.reserve_update(WORKER, ttl_seconds=30)
    assert jobs.inspect_update_hold(WORKER, token) == "active"
    assert jobs.inspect_update_hold("worker_87654321", token) == "absent"
    other = "a" * 64 if token != "a" * 64 else "b" * 64
    with pytest.raises(ValueError, match="mismatch"):
        jobs.inspect_update_hold(WORKER, other)
    now[0] = 131.
    assert jobs.inspect_update_hold(WORKER, token) == "expired"
    assert jobs.inspect_update_hold(WORKER, token) == "expired"
    with pytest.raises(ValueError, match="reserved"):
        jobs.assign(WORKER, "job_12345678", timeout_seconds=60)
    jobs.release_update_hold(WORKER, token)
    assert jobs.inspect_update_hold(WORKER, token) == "absent"
    assert jobs.state(WORKER) == "unknown"


def test_recovery_inspection_never_mutates_state(tmp_path):
    db = tmp_path / "read-only.db"
    now = [100.]
    jobs = initial_idle(db, now)
    token = jobs.reserve_update(WORKER, ttl_seconds=60)
    with sqlite3.connect(db) as conn:
        before = conn.execute(
            "SELECT hold_token,created_at,expires_at FROM worker_update_holds WHERE worker_id=?",
            (WORKER,)
        ).fetchone()
    assert jobs.inspect_update_hold(WORKER, token) == "active"
    assert jobs.inspect_update_hold(WORKER, token) == "active"
    with sqlite3.connect(db) as conn:
        after = conn.execute(
            "SELECT hold_token,created_at,expires_at FROM worker_update_holds WHERE worker_id=?",
            (WORKER,)
        ).fetchone()
    assert before == after
