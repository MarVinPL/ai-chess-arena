"""Arena-owned inference assignment transport, no duplicate game state on phone."""
import json
import sqlite3

import chess
import pytest

from remote_worker_inference_delivery import WorkerInferenceDelivery
from remote_worker_jobs import DurableWorkerJobs

JOB = {
    "version": 1,
    "job_id": "game_12345678",
    "fen": chess.STARTING_FEN,
    "model_sha256": "a" * 64,
    "max_tokens": 256,
    "timeout_seconds": 100,
}
WORKER = "android_1234567890abcdef"


def setup(tmp_path):
    path = tmp_path / "arena.sqlite"
    delivery = WorkerInferenceDelivery(path, clock=lambda: 1000.0)
    with sqlite3.connect(path) as db:
        db.execute(
            "INSERT INTO worker_job_safety VALUES (?,?,?,?,?)",
            (WORKER, None, "idle_confirmed", None, 1000.0),
        )
    return path, delivery


def test_assigned_job_is_replayed_read_only_to_same_worker(tmp_path):
    path, delivery = setup(tmp_path)
    assert delivery.assign_from_arena(WORKER, JOB) == JOB["job_id"]
    status = delivery.poll_assigned_job(WORKER)
    assert status == {"state": "assigned", "job": JOB}
    assert delivery.poll_assigned_job("android_other_worker") == {"state": "none"}
    assert delivery.poll_assigned_job(WORKER) == status
    with sqlite3.connect(path) as db:
        assert db.execute(
            "SELECT state,job_id,deadline FROM worker_job_safety WHERE worker_id=?",
            (WORKER,),
        ).fetchone() == ("running", JOB["job_id"], 1100.0)


def test_no_work_when_not_registered_or_idle(tmp_path):
    path, delivery = setup(tmp_path)
    with sqlite3.connect(path) as db:
        db.execute(
            "UPDATE worker_job_safety SET state='unknown' WHERE worker_id=?",
            (WORKER,),
        )
    with pytest.raises(ValueError):
        delivery.assign_from_arena(WORKER, JOB)
    assert delivery.poll_assigned_job(WORKER) == {"state": "none"}


def test_update_hold_blocks_assignment_and_job_reads(tmp_path):
    path, delivery = setup(tmp_path)
    with sqlite3.connect(path) as db:
        db.execute(
            "INSERT INTO worker_update_holds VALUES (?,?,?,?)",
            (WORKER, "b" * 64, 1000.0, 1300.0),
        )
    with pytest.raises(ValueError):
        delivery.assign_from_arena(WORKER, JOB)
    assert delivery.poll_assigned_job(WORKER) == {"state": "blocked"}


def test_pause_blocks_existing_inference_job(tmp_path):
    path, delivery = setup(tmp_path)
    delivery.assign_from_arena(WORKER, JOB)
    with sqlite3.connect(path) as db:
        db.execute(
            "INSERT INTO worker_update_pause_requests VALUES (?,?,?,?,?,?)",
            (WORKER, JOB["job_id"], "c" * 32, "requested", 1001.0, None),
        )
    assert delivery.poll_assigned_job(WORKER) == {"state": "blocked"}


def test_expired_lease_never_comes_back_to_life(tmp_path):
    path, delivery = setup(tmp_path)
    delivery.assign_from_arena(WORKER, JOB)
    late = WorkerInferenceDelivery(path, clock=lambda: 1100.0)
    assert late.poll_assigned_job(WORKER) == {"state": "expired"}
    assert delivery.poll_assigned_job(WORKER)["state"] == "assigned"
    with sqlite3.connect(path) as db:
        db.execute(
            "UPDATE worker_job_safety SET state='unknown' WHERE worker_id=?",
            (WORKER,),
        )
    assert delivery.poll_assigned_job(WORKER) == {"state": "blocked"}


def test_bad_job_does_not_create_lease(tmp_path):
    path, delivery = setup(tmp_path)
    for invalid in (
        {**JOB, "fen": "not chess"},
        {**JOB, "model_sha256": "abc"},
        {**JOB, "max_tokens": 100000},
        {**JOB, "timeout_seconds": 601},
        {**JOB, "job_id": "../escape"},
    ):
        with pytest.raises(ValueError):
            delivery.assign_from_arena(WORKER, invalid)
    with sqlite3.connect(path) as db:
        assert db.execute(
            "SELECT count(*) FROM worker_inference_assignments"
        ).fetchone()[0] == 0
        assert db.execute(
            "SELECT state FROM worker_job_safety WHERE worker_id=?",
            (WORKER,),
        ).fetchone()[0] == "idle_confirmed"


def test_existing_assignment_cannot_be_overwritten(tmp_path):
    path, delivery = setup(tmp_path)
    delivery.assign_from_arena(WORKER, JOB)
    with pytest.raises(ValueError):
        delivery.assign_from_arena(WORKER, {**JOB, "job_id": "game_99999999"})
    with sqlite3.connect(path) as db:
        assert db.execute(
            "SELECT count(*) FROM worker_inference_assignments"
        ).fetchone()[0] == 1


def test_transport_endpoint_has_no_worker_control_actions():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    server = (root / "server.py").read_text(encoding="utf-8")
    native = (root / "remote_worker_gui/src-tauri/src/lib.rs").read_text(encoding="utf-8")
    transport = (root / "remote_worker_gui/src-tauri/src/android_update_coordinator.rs").read_text(encoding="utf-8")
    section = server.split("if path == '/api/remote-workers/job-poll':", 1)[1].split(
        "if path == '/api/remote-workers/board-view':", 1
    )[0]
    assert "WorkerTokenStore(DB).authenticate(worker_id, token)" in section
    assert "WorkerInferenceDelivery(DB).poll_assigned_job(worker_id)" in section
    assert "assign_from_arena(" not in section
    assert '"job-poll"' in transport
    assert "async fn poll_my_inference_job(" in native
    assert "fetch_my_inference_job(" in native
