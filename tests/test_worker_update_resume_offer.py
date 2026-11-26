"""Private coordinator resume offers are NOT machine HTTP or automatic leases."""
import hashlib
import json

import pytest

from remote_worker_update_pause import WorkerUpdatePause


WORKER = "worker_12345678"
JOB = "job_12345678"
REQUEST = "a" * 32
HOLD = "b" * 64
CHECKPOINT = {
    "schema_version": 1,
    "job_id": JOB,
    "resume_mode": "restart",
    "payload": {"fen": "example", "moves": []},
}


def seed_resume(tmp_path):
    pause = WorkerUpdatePause(tmp_path / "resume.sqlite")
    raw = json.dumps(CHECKPOINT, sort_keys=True, ensure_ascii=False,
                     separators=(",", ":"), allow_nan=False)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    with pause.jobs._db() as db:
        db.execute("INSERT INTO worker_job_safety VALUES (?,NULL,'unknown',NULL,?)",
                   (WORKER, 100.0))
        db.execute(
            "INSERT INTO worker_update_pause_requests (worker_id,job_id,request_id,phase,created_at,acknowledged_at) VALUES (?,?,?,'acknowledged',?,?)",
            (WORKER, JOB, REQUEST, 100.0, 101.0))
        db.execute(
            "INSERT INTO worker_update_resume VALUES (?,?,?,?,?,?)",
            (WORKER, JOB, REQUEST, raw, digest, 101.0))
    return pause, digest


def offer(pause, **kwargs):
    return pause.prepare_trusted_resume_offer(
        WORKER,
        installed_identity_verified=True,
        stopped_engine_verified=True,
        checkpoint_verifier=lambda c: c == CHECKPOINT,
        **kwargs,
    )


def test_private_resume_offer_reuses_exact_durable_checkpoint_without_lease(tmp_path):
    pause, digest = seed_resume(tmp_path)
    before = pause.jobs.state(WORKER)
    first = offer(pause)
    second = offer(pause)
    assert first == second
    assert first["checkpoint"] == CHECKPOINT
    assert first["resume_mode"] == "restart"
    assert first["checkpoint_sha256"] == digest
    assert first["request_id"] == REQUEST
    assert before == pause.jobs.state(WORKER) == "unknown"
    assert pause.read_resume_checkpoint(WORKER) is not None
    assert pause.resume_status(WORKER)["resume_available"] is False
    assert pause.pending(WORKER)["phase"] == "acknowledged"


@pytest.mark.parametrize("attested,stopped", [
    (False, True), (True, False), (None, True), (True, None),
    (1, True), (True, 1),
])
def test_offer_requires_strict_trusted_device_and_engine_evidence(tmp_path, attested, stopped):
    pause, _ = seed_resume(tmp_path)
    with pytest.raises(ValueError, match="Trusted"):
        pause.prepare_trusted_resume_offer(
            WORKER, installed_identity_verified=attested,
            stopped_engine_verified=stopped,
            checkpoint_verifier=lambda c: True,
        )
    assert pause.read_resume_checkpoint(WORKER) is not None


def test_offer_rejects_outstanding_hold_or_other_maintenance(tmp_path):
    pause, _ = seed_resume(tmp_path)
    with pause.jobs._db() as db:
        db.execute("INSERT INTO worker_update_holds VALUES (?,?,?,?)",
                   (WORKER, HOLD, 100., 400.))
    with pytest.raises(ValueError, match="reserved|hold"):
        offer(pause)
    with pause.jobs._db() as db:
        db.execute("DELETE FROM worker_update_holds")
        db.execute("INSERT INTO worker_model_maintenance_holds VALUES (?,?,?,?)",
                   (WORKER, HOLD, 100., 400.))
    with pytest.raises(ValueError, match="maintenance|reserved|hold"):
        offer(pause)


def test_offer_fails_closed_on_mismatched_pause_worker_or_tampered_digest(tmp_path):
    pause, _ = seed_resume(tmp_path)
    with pause.jobs._db() as db:
        db.execute("UPDATE worker_update_pause_requests SET phase='requested'")
    with pytest.raises(ValueError, match="not reconciled"):
        offer(pause)
    with pause.jobs._db() as db:
        db.execute("UPDATE worker_update_pause_requests SET phase='acknowledged'")
        db.execute("UPDATE worker_job_safety SET state='running',job_id=?", (JOB,))
    with pytest.raises(ValueError, match="not reconciled"):
        offer(pause)
    with pause.jobs._db() as db:
        db.execute("UPDATE worker_job_safety SET state='unknown',job_id=NULL")
        db.execute("UPDATE worker_update_resume SET checkpoint_sha256=?", ("0" * 64,))
    with pytest.raises(ValueError, match="Corrupted"):
        offer(pause)


def test_offer_does_not_expose_checkpoint_via_http_or_skip_verifier(tmp_path):
    pause, _ = seed_resume(tmp_path)
    with pytest.raises(ValueError, match="rejected"):
        pause.prepare_trusted_resume_offer(
            WORKER, installed_identity_verified=True,
            stopped_engine_verified=True,
            checkpoint_verifier=lambda _: False,
        )
    assert pause.read_resume_checkpoint(WORKER) is not None
    from pathlib import Path
    server = (Path(__file__).resolve().parents[1] / "server.py").read_text()
    assert "prepare_trusted_resume_offer(" not in server


def test_resume_offer_only_becomes_running_after_verified_worker_acceptance(tmp_path):
    pause, _ = seed_resume(tmp_path)
    prepared = offer(pause)
    args = dict(
        checkpoint_sha256=prepared["checkpoint_sha256"],
        timeout_seconds=60,
        worker_ready_verified=True,
        resume_accepted_verified=False,
        checkpoint_verifier=lambda checkpoint: checkpoint == CHECKPOINT,
    )
    with pytest.raises(ValueError, match="acceptance"):
        pause.reconcile_resume(WORKER, JOB, REQUEST, **args)
    assert pause.jobs.state(WORKER) == "unknown"
    assert pause.read_resume_checkpoint(WORKER) is not None
    args["resume_accepted_verified"] = True
    resumed = pause.reconcile_resume(WORKER, JOB, REQUEST, **args)
    assert resumed == {"worker_id": WORKER, "job_id": JOB, "state": "running"}
    assert pause.jobs.state(WORKER) == "running"
    assert pause.read_resume_checkpoint(WORKER) is None
    assert pause.pending(WORKER) is None
    with pytest.raises(ValueError):
        pause.reconcile_resume(WORKER, JOB, REQUEST, **args)


def test_private_dispatch_requires_nonce_bound_verified_acceptance(tmp_path):
    pause, digest = seed_resume(tmp_path)
    sent = []
    def trusted_transport(offer):
        sent.append(offer.copy())
        return {
            "version": 1, "authenticated": True,
            "engine_accepted_verified": True,
            "worker_id": offer["worker_id"], "job_id": offer["job_id"],
            "request_id": offer["request_id"],
            "checkpoint_sha256": offer["checkpoint_sha256"],
            "resume_mode": offer["resume_mode"], "nonce": offer["nonce"],
        }
    result = pause.dispatch_trusted_resume(
        WORKER, installed_identity_verified=True, stopped_engine_verified=True,
        checkpoint_verifier=lambda c: c == CHECKPOINT,
        send_and_verify=trusted_transport, timeout_seconds=120)
    assert result["state"] == "running"
    assert sent[0]["version"] == 1
    assert sent[0]["checkpoint_sha256"] == digest
    assert len(sent[0]["nonce"]) == 32
    assert sent[0]["checkpoint"] == CHECKPOINT
    assert pause.read_resume_checkpoint(WORKER) is None
    with pytest.raises(ValueError):
        pause.dispatch_trusted_resume(
            WORKER, installed_identity_verified=True, stopped_engine_verified=True,
            checkpoint_verifier=lambda c: c == CHECKPOINT,
            send_and_verify=trusted_transport)


@pytest.mark.parametrize("mismatch", [
    {"nonce": "f" * 32}, {"job_id": "job_wrong000"},
    {"request_id": "c" * 32}, {"checkpoint_sha256": "0" * 64},
    {"resume_mode": "resume"}, {"worker_id": "worker_wrong000"},
    {"authenticated": False}, {"engine_accepted_verified": False},
    {"version": 2},
])
def test_private_resume_dispatch_rejects_wrong_receipt_without_state_change(tmp_path, mismatch):
    pause, _ = seed_resume(tmp_path)
    def untrusted_transport(offer):
        receipt = {
            "version": 1, "authenticated": True,
            "engine_accepted_verified": True, "worker_id": offer["worker_id"],
            "job_id": offer["job_id"], "request_id": offer["request_id"],
            "checkpoint_sha256": offer["checkpoint_sha256"],
            "resume_mode": offer["resume_mode"], "nonce": offer["nonce"],
        }
        receipt.update(mismatch)
        return receipt
    with pytest.raises(ValueError, match="receipt"):
        pause.dispatch_trusted_resume(
            WORKER, installed_identity_verified=True, stopped_engine_verified=True,
            checkpoint_verifier=lambda c: c == CHECKPOINT,
            send_and_verify=untrusted_transport)
    assert pause.jobs.state(WORKER) == "unknown"
    assert pause.read_resume_checkpoint(WORKER) is not None


def test_private_resume_dispatch_transport_failure_preserves_checkpoint(tmp_path):
    pause, _ = seed_resume(tmp_path)
    def broken(_):
        raise ConnectionError("network unavailable")
    with pytest.raises(ConnectionError):
        pause.dispatch_trusted_resume(
            WORKER, installed_identity_verified=True, stopped_engine_verified=True,
            checkpoint_verifier=lambda c: c == CHECKPOINT,
            send_and_verify=broken)
    assert pause.jobs.state(WORKER) == "unknown"
    assert pause.read_resume_checkpoint(WORKER) is not None


def test_private_resume_dispatch_not_exposed_via_worker_http_or_webview():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    assert "dispatch_trusted_resume(" not in (root / "server.py").read_text()
    assert "dispatch_trusted_resume(" not in (
        root / "remote_worker_gui/public/index.html").read_text()
