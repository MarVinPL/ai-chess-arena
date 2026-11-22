"""One-shot staged phone HTTP E2E; never writes to the real database."""
import json
import sqlite3
import threading
import time
from http.server import ThreadingHTTPServer
from urllib import error, request

import chess
import pytest

from remote_worker_phone_stage import (
    PhoneStage, StageHandler, arm_stage, prepare_stage, status_stage,
)
from remote_worker_tokens import WorkerTokenStore
from remote_worker_inference_delivery import WorkerInferenceDelivery
import tournament_checkpoint

WORKER = "android_stage_12345678"
SHA = "a" * 64
NONCE = "0123456789abcdef0123456789abcdef"


@pytest.fixture
def stage(tmp_path):
    source = tmp_path / "source.sqlite3"
    token = WorkerTokenStore(source).issue(WORKER, ttl_seconds=600)
    staging = tmp_path / "private" / "stage.sqlite3"
    prepare_stage(staging, source, WORKER, SHA, lifetime_seconds=300)
    assert not (staging.stat().st_mode & 0o077)
    assert status_stage(staging)["phase"] == "unarmed"
    assert WorkerTokenStore(staging).authenticate(WORKER, token)
    assert WorkerTokenStore(source).authenticate(WORKER, token)
    http = ThreadingHTTPServer(("127.0.0.1", 0), StageHandler)
    http.stage = PhoneStage(staging)
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()

    def send(route, body, *, method="POST"):
        req = request.Request(
            f"http://127.0.0.1:{http.server_port}{route}",
            data=json.dumps(body).encode() if method == "POST" else None,
            headers={"Content-Type": "application/json"},
            method=method,
        )
        try:
            with request.urlopen(req, timeout=3) as response:
                return response.status, json.load(response)
        except error.HTTPError as failure:
            return failure.code, json.load(failure)

    try:
        yield staging, source, token, send
    finally:
        http.shutdown()
        http.server_close()
        thread.join(timeout=3)


def credentials(token):
    return {"worker_id": WORKER, "token": token, "nonce": NONCE}


def wait_state(db, phase, limit=3):
    end = time.time() + limit
    while time.time() < end:
        result = status_stage(db)
        if result["phase"] == phase:
            return result
        time.sleep(0.02)
    raise AssertionError(f"stage did not reach {phase}, got {status_stage(db)}")


def test_http_one_move_atomic_checkpoint_and_no_production_db_changes(stage):
    db, source, token, send = stage
    cred = credentials(token)
    with sqlite3.connect(source) as prod:
        original_count = prod.execute("SELECT count(*) FROM worker_tokens").fetchone()[0]
    assert send("/api/remote-workers/authenticate", {
        "worker_id": WORKER, "token": token,
    })[1]["authenticated"] is True
    assert send("/api/remote-workers/authenticate", {
        "worker_id": WORKER, "token": "invalid-token-0000000000000000000000",
    })[0] == 401
    assert send("/api/remote-workers/job-poll", cred)[1]["state"] == "none"
    assert send("/api/remote-workers/ready", {**cred, "model_sha256": SHA})[1]["state"] == "ready"
    assert status_stage(db)["phase"] == "unarmed"
    arm_stage(db)

    assert send("/api/remote-workers/ready", {
        **cred, "model_sha256": "b" * 64,
    })[1]["state"] == "ready"
    assert status_stage(db)["phase"] == "armed"

    assert send("/api/remote-workers/ready", {
        **cred, "model_sha256": SHA,
    })[1]["state"] == "ready"
    assigned = wait_state(db, "assigned")
    assert assigned["job_id"].startswith("stage_")
    code, found = send("/api/remote-workers/job-poll", cred)
    assert code == 200 and found["state"] == "assigned"
    assert found["job"]["version"] == 2
    assert found["job"]["model_sha256"] == SHA
    assert found["job"]["fen"] == chess.STARTING_FEN
    assert found["job"]["legal_moves"] == sorted(m.uci() for m in chess.Board().legal_moves)
    assert send("/api/remote-workers/update-pause-poll", cred)[1]["instruction"] == {
        "action": "none",
    }
    code, view = send("/api/remote-workers/board-view", cred)
    assert code == 200 and view["active"] is True
    assert view["job_id"] == assigned["job_id"]

    code, invalid = send("/api/remote-workers/job-result", {
        **cred, "result": {"version": 1, "job_id": assigned["job_id"], "uci": "e2e5"},
    })
    assert code == 409 and "nonce" not in invalid
    assert tournament_checkpoint.load(db, 1)["moves"] == []

    code, accepted = send("/api/remote-workers/job-result", {
        **cred, "result": {"version": 1, "job_id": assigned["job_id"], "uci": "e2e4"},
    })
    assert code == 200 and accepted["state"] == "received_not_applied"
    completed = wait_state(db, "completed")
    assert [m["uci"] for m in completed["moves"]] == ["e2e4"]
    board = chess.Board()
    board.push_uci("e2e4")
    assert completed["moves"][0]["fen"] == board.fen()
    assert send("/api/remote-workers/job-poll", cred)[1]["state"] == "none"
    assert WorkerInferenceDelivery(db).jobs.state(WORKER) == "idle_confirmed"
    with sqlite3.connect(db) as dbconn:
        assert dbconn.execute("SELECT 1 FROM worker_inference_assignments").fetchone() is None
    assert send("/api/remote-workers/job-result", {
        **cred, "result": {"version": 1, "job_id": assigned["job_id"], "uci": "d2d4"},
    })[0] == 409
    assert status_stage(db)["phase"] == "completed"
    with sqlite3.connect(source) as prod:
        assert prod.execute("SELECT count(*) FROM worker_tokens").fetchone()[0] == original_count
        assert prod.execute(
            "SELECT 1 FROM sqlite_master WHERE name='worker_job_safety'"
        ).fetchone() is None


def test_bad_auth_nonce_and_no_second_arm(stage):
    db, _, token, send = stage
    cred = credentials(token)
    assert send("/api/remote-workers/job-poll", {
        **cred, "token": "intentionally-wrong-token-000000000000",
    })[0] == 401
    code, response = send("/api/remote-workers/ready", {
        **cred, "nonce": "bad", "model_sha256": SHA,
    })
    assert code == 400 and "nonce" not in response
    assert status_stage(db)["phase"] == "unarmed"
    arm_stage(db)
    with pytest.raises(ValueError):
        arm_stage(db)


def test_no_remote_exposure_or_prod_db_path():
    import inspect
    from remote_worker_phone_stage import serve, StageHandler
    source = inspect.getsource(serve)
    assert "127.0.0.1" in source and "0.0.0.0" not in source
    assert "/api/remote-workers/assign" not in inspect.getsource(StageHandler)


def test_checkpoint_crash_rolls_back_without_releasing_worker(stage, monkeypatch):
    """Even a failure AFTER checkpoint update must roll back result and lease."""
    db, _, token, send = stage
    auth = credentials(token)
    arm_stage(db)
    assert send("/api/remote-workers/ready", {
        **auth, "model_sha256": SHA,
    })[0] == 200
    assigned = wait_state(db, "assigned")
    delivery = WorkerInferenceDelivery(db)
    job_id = assigned["job_id"]
    assert delivery.submit_inference_result(WORKER, {
        "version": 1, "job_id": job_id, "uci": "e2e4"
    })["state"] == "received_not_applied"
    original = tournament_checkpoint.load(db, 1)
    board = chess.Board()
    board.push_uci("e2e4")
    updated = {
        **original,
        "moves": [{"uci": "e2e4", "ply": 1, "fen": board.fen()}],
        "move_times": [0.0],
        "fen": board.fen(),
        "side_to_move": "black",
    }
    actual_save = tournament_checkpoint.save_in_transaction

    def abort_after_sql_write(*args, **kwargs):
        actual_save(*args, **kwargs)
        raise OSError("simulated process failure after checkpoint update")

    monkeypatch.setattr(
        tournament_checkpoint, "save_in_transaction", abort_after_sql_write,
    )
    with pytest.raises(OSError, match="simulated process failure"):
        delivery.finalize_arena_result(
            WORKER, job_id, "e2e4", tournament_id=1, checkpoint=updated,
        )
    assert tournament_checkpoint.load(db, 1) == original
    assert delivery.read_arena_result(WORKER, job_id) == "e2e4"
    assert delivery.jobs.state(WORKER) == "running"
    assert status_stage(db)["phase"] == "assigned"


def test_stage_forbids_prod_db_in_http_server():
    import inspect
    from remote_worker_phone_stage import serve
    content = inspect.getsource(serve)
    assert '/var/lib/ai-chess-arena/arena.sqlite3' in content
    assert 'Staging directory is not private' in content
