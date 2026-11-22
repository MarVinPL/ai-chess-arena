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
    assert 'response.get("nonce").and_then(Value::as_str) != Some(nonce.as_str())' in COORDINATOR


def test_single_button_does_not_claim_game_resumed_without_executor():
    assert 'id="resumeWorkerGame"' in GUI
    assert 'id="resumeWorkerGame" type="button" disabled' in GUI
    assert "invoke('inspect_worker_resume_readiness'" in GUI
    assert "button.disabled=true" in GUI
    assert "Checkpoint partii jest zachowany." in GUI
    assert "async fn inspect_worker_resume_readiness(" in NATIVE
    assert "android_update_coordinator::fetch_resume_status(" in NATIVE
