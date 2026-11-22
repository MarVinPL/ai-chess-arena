"""Fail-closed post-update Resume state: a checkpoint is not a running game."""
from pathlib import Path
from remote_worker_update_pause import WorkerUpdatePause

ROOT = Path(__file__).resolve().parents[1]
SERVER = (ROOT / "server.py").read_text(encoding="utf-8")
COORDINATOR = (ROOT / "remote_worker_gui/src-tauri/src/android_update_coordinator.rs").read_text(encoding="utf-8")
NATIVE = (ROOT / "remote_worker_gui/src-tauri/src/lib.rs").read_text(encoding="utf-8")
GUI = (ROOT / "remote_worker_gui/public/index.html").read_text(encoding="utf-8")


def test_no_checkpoint_does_not_enable_resume(tmp_path):
    pause = WorkerUpdatePause(tmp_path / "jobs.sqlite")
    state = pause.resume_status("worker_12345678")
    assert state == {"state": "no_checkpoint", "resume_available": False}


def test_saved_checkpoint_never_becomes_runnable_without_executor(tmp_path):
    import hashlib
    import json
    pause = WorkerUpdatePause(tmp_path / "jobs.sqlite")
    payload = {
        "schema_version": 1,
        "job_id": "job_12345678",
        "resume_mode": "restart",
        "payload": {},
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False,
                     separators=(",", ":"), allow_nan=False)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    with pause.jobs._db() as db:
        db.execute(
            "INSERT INTO worker_update_resume VALUES (?,?,?,?,?,?)",
            ("worker_12345678", "job_12345678", "a" * 32, raw, digest, 1000),
        )
        db.execute(
            "INSERT INTO worker_update_pause_requests VALUES (?,?,?,?,?,?)",
            ("worker_12345678", "job_12345678", "a" * 32,
             "acknowledged", 1000, 1000),
        )
        db.execute(
            "INSERT INTO worker_job_safety VALUES (?,?,?,?,?)",
            ("worker_12345678", None, "unknown", None, 1000),
        )
        db.execute(
            "INSERT INTO worker_update_holds VALUES (?,?,?,?)",
            ("worker_12345678", "b" * 64, 1000, 1300),
        )
    status = pause.resume_status("worker_12345678")
    assert status["state"] == "waiting_update_reconciliation"
    assert status["resume_available"] is False
    assert status["checkpoint_sha256"] == digest
    assert "checkpoint" not in status
    assert "request_id" not in status
    with pause.jobs._db() as db:
        db.execute("DELETE FROM worker_update_holds WHERE worker_id=?",
                   ("worker_12345678",))
    status = pause.resume_status("worker_12345678")
    assert status["state"] == "awaiting_local_engine"
    assert status["resume_available"] is False
    with pause.jobs._db() as db:
        db.execute(
            "UPDATE worker_job_safety SET state='idle_confirmed' WHERE worker_id=?",
            ("worker_12345678",),
        )
    status = pause.resume_status("worker_12345678")
    assert status["state"] == "blocked"
    assert status["resume_available"] is False


def test_machine_authenticated_resume_status_has_no_unsafe_dispatch():
    section = SERVER.split("if path == '/api/remote-workers/update-resume-status':", 1)[1].split(
        "if path == '/api/remote-workers/update-pause-poll':", 1
    )[0]
    assert "WorkerTokenStore(DB).authenticate(worker_id, token)" in section
    assert "WorkerUpdatePause(DB).resume_status(worker_id)" in section
    assert "'resume_available': False" in section
    assert "reconcile_resume(" not in section
    assert "checkpoint_json" not in section
    assert '"update-resume-status"' in COORDINATOR
    assert 'value.get("nonce").and_then(Value::as_str) != Some(nonce.as_str())' in COORDINATOR


def test_single_button_does_not_claim_game_resumed_without_executor():
    assert 'id="resumeWorkerGame"' in GUI
    assert 'id="resumeWorkerGame" type="button" disabled' in GUI
    assert "invoke('inspect_worker_resume_readiness'" in GUI
    assert "button.disabled=true" in GUI
    assert "Checkpoint przerwanego zadania jest zachowany w Arenie." in GUI
    assert "resumeReadinessState==='no_checkpoint'" in GUI
    assert "state!=='no_checkpoint'||safety!=='idle_confirmed'||!supported" in GUI
    assert "const authenticated=await invoke('verify_worker_connection'" in GUI
    assert "if(!activatePhoneJobPolling())" in GUI
    assert "async fn inspect_worker_resume_readiness(" in NATIVE
    assert "android_update_coordinator::fetch_resume_status(" in NATIVE


def test_resume_button_never_replays_durable_interrupted_job_unverified():
    # A one-tap continuation after an IDLE app update is only restarting
    # assignment polling. A genuinely pending interrupted checkpoint remains
    # blocked until an authenticated resume dispatch/ACK exists.
    assert "button.disabled=!(state==='no_checkpoint'&&supported===true)" in GUI
    assert "if(!invoke||resumeReadinessState!=='no_checkpoint')return;" in GUI
    assert "check_update_safety" in GUI
    assert "Odbieranie zadań wznowione na telefonie." in GUI
    assert "resume_accepted_verified" in (ROOT / "remote_worker_update_pause.py").read_text(
        encoding="utf-8")
