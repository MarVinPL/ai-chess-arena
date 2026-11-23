"""Atomic coordinator model maintenance fence is not GGUF deletion permission."""
import sqlite3

import pytest

from remote_worker_inference_delivery import WorkerInferenceDelivery

WORKER = "android_1234567890abcdef"
SHA = "a" * 64


def setup(tmp_path):
    path = tmp_path / "arena.sqlite"
    delivery = WorkerInferenceDelivery(path, clock=lambda: 1000.0)
    assert delivery.register_local_ready(WORKER, SHA) == "ready"
    return path, delivery


def test_maintenance_hold_fences_assignments_and_readiness(tmp_path):
    path, d = setup(tmp_path)
    token = d.jobs.reserve_model_maintenance(WORKER)
    assert len(token) == 64
    assert d.find_ready_worker(SHA) is None
    assert d.poll_assigned_job(WORKER) == {"state": "blocked"}
    assert d.jobs.update_safety_snapshot(WORKER)[0] == "unknown"
    with pytest.raises(ValueError):
        d.register_local_ready(WORKER, SHA)
    with pytest.raises(ValueError):
        d.jobs.reserve_model_maintenance(WORKER)
    with sqlite3.connect(path) as db:
        assert db.execute(
            "SELECT count(*) FROM worker_model_maintenance_holds"
        ).fetchone()[0] == 1


def test_maintenance_release_requires_original_token_and_reconciliation(tmp_path):
    path, d = setup(tmp_path)
    token = d.jobs.reserve_model_maintenance(WORKER)
    with pytest.raises(ValueError):
        d.jobs.release_model_maintenance(WORKER, "f" * 64)
    assert d.find_ready_worker(SHA) is None
    d.jobs.release_model_maintenance(WORKER, token)
    assert d.jobs.update_safety_snapshot(WORKER)[0] == "unknown"
    assert d.find_ready_worker(SHA) is None
    with sqlite3.connect(path) as db:
        assert db.execute(
            "SELECT state FROM worker_job_safety WHERE worker_id=?", (WORKER,)
        ).fetchone()[0] == "unknown"
    with pytest.raises(ValueError):
        d.jobs.release_model_maintenance(WORKER, token)


def test_active_running_or_stale_lease_cannot_be_reserved(tmp_path):
    path, d = setup(tmp_path)
    with sqlite3.connect(path) as db:
        db.execute(
            "UPDATE worker_job_safety SET state='running', job_id=?, deadline=? "
            "WHERE worker_id=?", ("game_12345678", 1100, WORKER)
        )
    with pytest.raises(ValueError):
        d.jobs.reserve_model_maintenance(WORKER)
    with sqlite3.connect(path) as db:
        db.execute(
            "UPDATE worker_job_safety SET state='idle_confirmed', job_id=NULL, "
            "deadline=NULL,updated_at=? WHERE worker_id=?", (960, WORKER)
        )
    with pytest.raises(ValueError):
        d.jobs.reserve_model_maintenance(WORKER)


def test_stop_ack_and_update_holds_block_cache_maintenance(tmp_path):
    path, d = setup(tmp_path)
    with sqlite3.connect(path) as db:
        db.execute(
            "INSERT INTO worker_update_pause_requests VALUES (?,?,?,?,?,?)",
            (WORKER, "game_12345678", "b" * 32, "acknowledged", 990, 995)
        )
    with pytest.raises(ValueError):
        d.jobs.reserve_model_maintenance(WORKER)
    with sqlite3.connect(path) as db:
        db.execute("DELETE FROM worker_update_pause_requests")
        db.execute("INSERT INTO worker_update_holds VALUES (?,?,?,?)",
                   (WORKER, "c" * 64, 900, 950))
    with pytest.raises(ValueError):
        d.jobs.reserve_model_maintenance(WORKER)


def test_expired_maintenance_hold_is_still_durable_block(tmp_path):
    path, d = setup(tmp_path)
    token = d.jobs.reserve_model_maintenance(WORKER, ttl_seconds=30)
    late = WorkerInferenceDelivery(path, clock=lambda: 2000.0)
    assert late.find_ready_worker(SHA) is None
    with pytest.raises(ValueError):
        late.jobs.reserve_model_maintenance(WORKER)
    late.jobs.release_model_maintenance(WORKER, token)
    assert late.jobs.update_safety_snapshot(WORKER)[0] == "unknown"


def test_atomic_maintenance_reservation_loses_to_unresolved_assignment(tmp_path):
    path, d = setup(tmp_path)
    # Simulate a racing coordinator already committing an assignment.
    with sqlite3.connect(path) as db:
        db.execute(
            "INSERT INTO worker_inference_assignments VALUES (?,?,?,?)",
            (WORKER, "game_12345678", "{}", 1000)
        )
    with pytest.raises(ValueError, match="Unresolved inference assignment"):
        d.jobs.reserve_model_maintenance(WORKER)


def test_maintenance_fence_prevents_real_inference_assignment(tmp_path):
    import chess
    path, d = setup(tmp_path)
    token = d.jobs.reserve_model_maintenance(WORKER)
    job = {
        "version": 1, "job_id": "game_12345678",
        "fen": chess.STARTING_FEN, "model_sha256": SHA,
        "max_tokens": 32, "timeout_seconds": 100,
    }
    with pytest.raises(ValueError, match="maintenance"):
        d.assign_from_arena(WORKER, job)
    with sqlite3.connect(path) as db:
        assert db.execute(
            "SELECT count(*) FROM worker_inference_assignments"
        ).fetchone()[0] == 0
    d.jobs.release_model_maintenance(WORKER, token)
    # Explicit release is not idle evidence: re-assignment still fails.
    with pytest.raises(ValueError):
        d.assign_from_arena(WORKER, job)
