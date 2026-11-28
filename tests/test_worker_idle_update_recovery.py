"""Only an explicitly released IDLE APK update may recover Android Worker."""
import sqlite3
import pytest
from remote_worker_inference_delivery import WorkerInferenceDelivery

WORKER = "android_1234567890abcdef"
MODEL = "a" * 64
TOKEN = "b" * 64


def prepared(tmp_path, now=1000.0):
    path = tmp_path / "workers.sqlite"
    delivery = WorkerInferenceDelivery(path, clock=lambda: now)
    assert delivery.register_local_ready(WORKER, MODEL) == "ready"
    return path, delivery


def status(path):
    with sqlite3.connect(path) as db:
        row = db.execute(
            "SELECT state,job_id,deadline FROM worker_job_safety WHERE worker_id=?",
            (WORKER,),
        ).fetchone()
        proof = db.execute(
            "SELECT released_at FROM worker_idle_update_recovery WHERE worker_id=?",
            (WORKER,),
        ).fetchone()
    return row, proof


def test_hold_release_then_local_model_proof_restores_idle_once(tmp_path):
    path, delivery = prepared(tmp_path)
    assert delivery.jobs.reserve_update(WORKER, hold_token=TOKEN) == TOKEN
    with pytest.raises(ValueError):
        delivery.register_local_ready(WORKER, MODEL)
    with pytest.raises(ValueError):
        delivery.jobs.release_update_hold(WORKER, "c" * 64)
    delivery.jobs.release_update_hold(WORKER, TOKEN)
    assert status(path) == (("unknown", None, None), (1000.0,))
    assert delivery.find_ready_worker(MODEL) is None
    assert delivery.register_local_ready(WORKER, MODEL) == "ready"
    assert status(path) == (("idle_confirmed", None, None), None)
    with pytest.raises(ValueError):
        delivery.jobs.release_update_hold(WORKER, TOKEN)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE worker_job_safety SET state='unknown' WHERE worker_id=?", (WORKER,))
    assert delivery.register_local_ready(WORKER, MODEL) == "blocked"
    assert status(path)[1] is None


def test_unknown_without_verified_update_can_never_claim_idle(tmp_path):
    path, delivery = prepared(tmp_path)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE worker_job_safety SET state='unknown' WHERE worker_id=?", (WORKER,))
    assert delivery.register_local_ready(WORKER, MODEL) == "blocked"
    assert status(path) == (("unknown", None, None), None)


def test_expired_idle_update_receipt_is_not_runnable(tmp_path):
    path, delivery = prepared(tmp_path)
    delivery.jobs.reserve_update(WORKER, hold_token=TOKEN)
    delivery.jobs.release_update_hold(WORKER, TOKEN)
    late = WorkerInferenceDelivery(path, clock=lambda: 1000.0 + 86401)
    assert late.register_local_ready(WORKER, MODEL) == "blocked"
    assert status(path)[0] == ("unknown", None, None)


def test_paused_update_never_creates_idle_recovery_proof(tmp_path):
    path, delivery = prepared(tmp_path)
    delivery.jobs.reserve_update(WORKER, hold_token=TOKEN)
    with sqlite3.connect(path) as db:
        db.execute(
            "INSERT INTO worker_update_pause_requests (worker_id,job_id,request_id,phase,created_at,acknowledged_at) VALUES (?,?,?,?,?,?)",
            (WORKER, "job_12345678", "c" * 32, "acknowledged", 1000.0, 1000.0),
        )
    delivery.jobs.release_update_hold(WORKER, TOKEN)
    assert status(path) == (("unknown", None, None), None)
    with pytest.raises(ValueError):
        delivery.register_local_ready(WORKER, MODEL)


def test_busy_update_does_not_create_idle_receipt(tmp_path):
    path, delivery = prepared(tmp_path)
    delivery.jobs.reserve_update(WORKER, hold_token=TOKEN)
    with sqlite3.connect(path) as db:
        db.execute(
            "UPDATE worker_job_safety SET job_id=?,state='running',deadline=? WHERE worker_id=?",
            ("job_12345678", 1200, WORKER),
        )
    delivery.jobs.release_update_hold(WORKER, TOKEN)
    assert status(path) == (("unknown", None, None), None)
    assert delivery.register_local_ready(WORKER, MODEL) == "blocked"


def test_android_idle_resume_never_executes_inference_before_recovery():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    native = (root / "remote_worker_gui/src-tauri/src/lib.rs").read_text("utf8")
    ui = (root / "remote_worker_gui/public/index.html").read_text("utf8")
    method = native.split("async fn reconcile_idle_phone_after_update(", 1)[1].split(
        "/// Read coordinator resume readiness", 1
    )[0]
    assert 'resume != "no_checkpoint"' in method
    assert '!= Some("none")' in method
    assert "probe_local_model_alias().await?" in method
    assert "announce_phone_ready(" in method
    assert "infer_local_uci(" not in method
    assert "submit_my_inference_move(" not in method
    assert "invoke('reconcile_idle_phone_after_update'" in ui
