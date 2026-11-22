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
    assert delivery.register_local_ready(WORKER, JOB["model_sha256"]) == "ready"
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


def test_legal_move_is_received_but_not_applied_to_arena(tmp_path):
    path, delivery = setup(tmp_path)
    delivery.assign_from_arena(WORKER, JOB)
    result = {"version": 1, "job_id": JOB["job_id"], "uci": "e2e4"}
    assert delivery.submit_inference_result(WORKER, result) == {
        "state": "received_not_applied", "job_id": JOB["job_id"]
    }
    assert delivery.submit_inference_result(WORKER, result) == {
        "state": "already_received", "job_id": JOB["job_id"]
    }
    with sqlite3.connect(path) as db:
        assert db.execute(
            "SELECT result_json FROM worker_inference_results WHERE job_id=?",
            (JOB["job_id"],),
        ).fetchone()[0] == json.dumps(
            result, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        # Only Arena's tournament runner may apply UCI and close its lease.
        assert db.execute(
            "SELECT state FROM worker_job_safety WHERE worker_id=?", (WORKER,)
        ).fetchone()[0] == "running"


def test_illegal_wrong_or_changed_result_cannot_replace_first_move(tmp_path):
    path, delivery = setup(tmp_path)
    delivery.assign_from_arena(WORKER, JOB)
    for invalid in (
        {"version": 1, "job_id": JOB["job_id"], "uci": "e2e5"},
        {"version": 1, "job_id": "game_othergame", "uci": "e2e4"},
        {"version": 1, "job_id": JOB["job_id"], "uci": "e9e4"},
        {"version": 1, "job_id": JOB["job_id"], "uci": "e2e4", "fen": JOB["fen"]},
    ):
        with pytest.raises(ValueError):
            delivery.submit_inference_result(WORKER, invalid)
    good = {"version": 1, "job_id": JOB["job_id"], "uci": "e2e4"}
    delivery.submit_inference_result(WORKER, good)
    with pytest.raises(ValueError):
        delivery.submit_inference_result(
            WORKER, {"version": 1, "job_id": JOB["job_id"], "uci": "d2d4"}
        )
    with sqlite3.connect(path) as db:
        assert db.execute(
            "SELECT count(*) FROM worker_inference_results"
        ).fetchone()[0] == 1


def test_expired_lease_and_update_barriers_reject_submissions(tmp_path):
    path, delivery = setup(tmp_path)
    delivery.assign_from_arena(WORKER, JOB)
    late = WorkerInferenceDelivery(path, clock=lambda: 1100.0)
    good = {"version": 1, "job_id": JOB["job_id"], "uci": "e2e4"}
    with pytest.raises(ValueError):
        late.submit_inference_result(WORKER, good)
    with sqlite3.connect(path) as db:
        db.execute(
            "INSERT INTO worker_update_holds VALUES (?,?,?,?)",
            (WORKER, "b" * 64, 1000.0, 1300.0),
        )
    with pytest.raises(ValueError):
        delivery.submit_inference_result(WORKER, good)


def test_result_api_is_authenticated_and_never_applies_moves():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    server = (root / "server.py").read_text(encoding="utf-8")
    rust = (root / "remote_worker_gui/src-tauri/src/android_update_coordinator.rs").read_text(encoding="utf-8")
    block = server.split("if path == '/api/remote-workers/job-result':", 1)[1].split(
        "if path == '/api/remote-workers/job-poll':", 1
    )[0]
    assert "WorkerTokenStore(DB).authenticate(worker_id, token)" in block
    assert "submit_inference_result(" in block
    assert "set_live(" not in block
    assert "board.push" not in block
    assert '"job-result"' in rust
    assert "submit_my_inference_move(" in rust


def test_android_ui_polls_only_its_assigned_job_and_does_not_claim_inference():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    ui = (root / "remote_worker_gui/public/index.html").read_text(encoding="utf-8")
    assert "invoke('poll_my_inference_job'" in ui
    assert 'id="myAssignedModel"' in ui
    assert 'id="myAssignedGame"' in ui
    assert 'id="myAssignedStatus"' in ui
    assert "Przydzielone — oczekiwanie na wykonawcę telefonu" in ui
    assert "document.hidden" in ui
    assert "setInterval(()=>{void refreshMyInferenceAssignment();},8000)" in ui


def test_requires_recent_matching_on_phone_model_before_assignment(tmp_path):
    path = tmp_path / "ready.sqlite"
    delivery = WorkerInferenceDelivery(path, clock=lambda: 1000.0)
    with pytest.raises(ValueError):
        delivery.assign_from_arena(WORKER, JOB)
    with pytest.raises(ValueError):
        delivery.register_local_ready(WORKER, "x" * 64)
    assert delivery.register_local_ready(WORKER, "b" * 64) == "ready"
    with pytest.raises(ValueError):
        delivery.assign_from_arena(WORKER, JOB)
    assert delivery.register_local_ready(WORKER, JOB["model_sha256"]) == "ready"
    stale = WorkerInferenceDelivery(path, clock=lambda: 1031.0)
    with pytest.raises(ValueError):
        stale.assign_from_arena(WORKER, JOB)
    # Registered model is now fresh again.
    assert stale.register_local_ready(WORKER, JOB["model_sha256"]) == "ready"
    assert stale.assign_from_arena(WORKER, JOB) == JOB["job_id"]
    assert stale.register_local_ready(WORKER, JOB["model_sha256"]) == "blocked"


def test_ready_endpoint_is_machine_authenticated_not_an_assignment():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    server = (root / "server.py").read_text(encoding="utf-8")
    coordinator = (
        root / "remote_worker_gui/src-tauri/src/android_update_coordinator.rs"
    ).read_text(encoding="utf-8")
    section = server.split("if path == '/api/remote-workers/ready':", 1)[1].split(
        "if path == '/api/remote-workers/job-result':", 1
    )[0]
    assert "WorkerTokenStore(DB).authenticate(worker_id, token)" in section
    assert "register_local_ready(" in section
    assert "assign_from_arena" not in section
    assert "'nonce': nonce" in section
    assert "async fn announce_phone_ready(" in coordinator
    assert 'model_sha256.len() != 64' in coordinator


def test_unknown_or_failed_worker_lease_cannot_be_rearmed_by_phone_ready(tmp_path):
    path, delivery = setup(tmp_path)
    for state in ("unknown", "failed"):
        with sqlite3.connect(path) as db:
            db.execute(
                "UPDATE worker_job_safety SET state=?,job_id=NULL WHERE worker_id=?",
                (state, WORKER),
            )
        assert delivery.register_local_ready(WORKER, JOB["model_sha256"]) == "blocked"
        with sqlite3.connect(path) as db:
            assert db.execute(
                "SELECT state,job_id FROM worker_job_safety WHERE worker_id=?",
                (WORKER,),
            ).fetchone() == (state, None)
        with pytest.raises(ValueError):
            delivery.assign_from_arena(WORKER, JOB)


def test_first_authenticated_ready_registration_is_idle_not_running(tmp_path):
    path = tmp_path / "empty.sqlite"
    delivery = WorkerInferenceDelivery(path, clock=lambda: 1000.0)
    assert delivery.register_local_ready(WORKER, JOB["model_sha256"]) == "ready"
    with sqlite3.connect(path) as db:
        assert db.execute(
            "SELECT state,job_id FROM worker_job_safety WHERE worker_id=?",
            (WORKER,),
        ).fetchone() == ("idle_confirmed", None)
