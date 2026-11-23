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
            "INSERT INTO worker_update_pause_requests VALUES (?,?,?,'acknowledged',?,?)",
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
