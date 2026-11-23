"""Real HTTP gateway and atomic checkpoint in an isolated SQLite tournament."""
import hashlib
import json
import sqlite3
import threading
from http.server import ThreadingHTTPServer
from urllib import request, error

import chess
import pytest
import server
import tournament_checkpoint
from remote_worker_inference_delivery import WorkerInferenceDelivery
from remote_worker_phone_sandbox import (
    SandboxHandler, MODEL_SHA, arm_one_job, finalize_one_result,
    prepare_sandbox, SANDBOX_TOURNAMENT_ID,
)
from remote_worker_tokens import WorkerTokenStore

WORKER = "android_test_12345678"
NONCE = "0123456789abcdef0123456789abcdef"


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    source = tmp_path / "production-reference.sqlite3"
    token = WorkerTokenStore(source).issue(WORKER, ttl_seconds=600)
    source_before = hashlib.sha256(source.read_bytes()).hexdigest()
    destination = tmp_path / "isolation" / "arena.sqlite3"
    prepare_sandbox(destination, source, WORKER)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == source_before
    assert WorkerTokenStore(destination).authenticate(WORKER, token)
    with sqlite3.connect(destination) as con:
        assert con.execute("SELECT COUNT(*) FROM worker_tokens").fetchone()[0] == 1
    monkeypatch.setattr(server, "DB", destination)
    monkeypatch.setenv("ARENA_PHONE_WORKER_API_ENABLED", "1")
    monkeypatch.setenv("ARENA_PHONE_INFERENCE_ENABLED", "0")
    api = ThreadingHTTPServer(("127.0.0.1", 0), SandboxHandler)
    thread = threading.Thread(target=api.serve_forever, daemon=True)
    thread.start()

    def post(route, data):
        req = request.Request(
            f"http://127.0.0.1:{api.server_port}/api/remote-workers/{route}",
            json.dumps(data).encode(),
            {"Content-Type": "application/json"}, method="POST"
        )
        try:
            with request.urlopen(req, timeout=4) as response:
                return response.status, json.load(response)
        except error.HTTPError as exc:
            try:
                return exc.code, json.load(exc)
            except ValueError:
                return exc.code, {}

    try:
        yield destination, token, post
    finally:
        api.shutdown()
        api.server_close()
        thread.join(timeout=4)


def auth(token):
    return {"worker_id": WORKER, "token": token, "nonce": NONCE}


def test_no_external_admin_or_public_data(sandbox):
    _, token, post = sandbox
    assert post("authenticate", {"worker_id": WORKER, "token": token}) == (
        200, {"authenticated": True, "worker_id": WORKER}
    )
    assert post("authenticate", {"worker_id": WORKER, "token": "not-a-real-token-00000000000"})[0] == 401
    status, reply = post("job-poll", auth("not-a-real-token-00000000000"))
    assert status == 401 and "nonce" not in reply
    assert post("admin-assign", auth(token))[0] == 404
    assert post("update-release", auth(token))[0] == 404
    assert post("tournaments/start", auth(token))[0] == 404
    assert post("job-poll", {**auth(token), "nonce": "bad"})[0] == 400


def test_real_http_job_result_and_transactional_checkpoint(sandbox):
    path, token, post = sandbox
    who = auth(token)
    assert post("job-poll", who)[1]["state"] == "none"
    assert post("ready", {**who, "model_sha256": MODEL_SHA})[1]["state"] == "ready"
    job_id = arm_one_job(path, WORKER)
    code, job_reply = post("job-poll", who)
    assert code == 200 and job_reply["state"] == "assigned"
    job = job_reply["job"]
    assert job["job_id"] == job_id and job["version"] == 2
    assert job["legal_moves"] == sorted(move.uci() for move in chess.Board().legal_moves)
    assert post("job-result", {**who, "result": {
        "version": 1, "job_id": job_id, "uci": "e2e5"
    }})[0] == 409
    code, accepted = post("job-result", {**who, "result": {
        "version": 1, "job_id": job_id, "uci": "e2e4"
    }})
    assert code == 200 and accepted["state"] == "received_not_applied"
    assert post("job-poll", who)[1]["state"] == "awaiting_arena"
    old = tournament_checkpoint.load(path, SANDBOX_TOURNAMENT_ID)
    assert old["moves"] == [] and old["fen"] == chess.STARTING_FEN
    assert finalize_one_result(path, WORKER) == "e2e4"
    new = tournament_checkpoint.load(path, SANDBOX_TOURNAMENT_ID)
    assert new["moves"][0]["uci"] == "e2e4"
    board = chess.Board()
    board.push_uci("e2e4")
    assert new["fen"] == board.fen()
    assert post("job-poll", who)[1]["state"] == "none"
    with pytest.raises(ValueError, match="No pending sandbox job"):
        finalize_one_result(path, WORKER)
    with pytest.raises(ValueError, match="already completed"):
        arm_one_job(path, WORKER)


def test_failed_checkpoint_keeps_result_and_running_lease(sandbox):
    path, token, post = sandbox
    who = auth(token)
    assert post("ready", {**who, "model_sha256": MODEL_SHA})[1]["state"] == "ready"
    job_id = arm_one_job(path, WORKER)
    assert post("job-result", {**who, "result": {
        "version": 1, "job_id": job_id, "uci": "d2d4"
    }})[0] == 200
    delivery = WorkerInferenceDelivery(path)
    original = tournament_checkpoint.load(path, SANDBOX_TOURNAMENT_ID)
    with pytest.raises(ValueError, match="does not extend"):
        delivery.finalize_arena_result(
            WORKER, job_id, "d2d4", tournament_id=SANDBOX_TOURNAMENT_ID,
            checkpoint={**original, "fen": chess.STARTING_FEN, "moves": []},
        )
    with sqlite3.connect(path) as con:
        assert con.execute(
            "SELECT state FROM worker_job_safety WHERE worker_id=?", (WORKER,)
        ).fetchone()[0] == "running"
        assert con.execute(
            "SELECT COUNT(*) FROM worker_inference_results WHERE worker_id=?", (WORKER,)
        ).fetchone()[0] == 1
    assert tournament_checkpoint.load(path, SANDBOX_TOURNAMENT_ID) == original
    assert finalize_one_result(path, WORKER) == "d2d4"


def test_stop_request_fences_http_result_without_fake_ack_or_checkpoint(sandbox):
    """Issue #288: HTTP STOP delivery is NOT a worker STOP ACK."""
    from remote_worker_update_pause import WorkerUpdatePause

    path, token, post = sandbox
    who = auth(token)
    assert post("ready", {**who, "model_sha256": MODEL_SHA})[1]["state"] == "ready"
    job_id = arm_one_job(path, WORKER)
    old_checkpoint = tournament_checkpoint.load(path, SANDBOX_TOURNAMENT_ID)

    pause = WorkerUpdatePause(path)
    request_id = pause.request(WORKER, job_id)
    assert len(request_id) == 32

    # A malicious client with the wrong credential or nonce cannot read STOP.
    assert post("update-pause-poll", auth("not-a-real-token-00000000000"))[0] == 401
    assert post("update-pause-poll", {**who, "nonce": "invalid"})[0] == 400

    for _ in range(2):
        code, response = post("update-pause-poll", who)
        assert code == 200
        assert response["instruction"] == {
            "action": "stop_checkpoint",
            "job_id": job_id,
            "request_id": request_id,
        }
        assert response["install_allowed"] is False

    # Even a perfectly legal but late UCI is rejected after STOP was queued.
    assert post("job-poll", who)[1]["state"] == "blocked"
    status, reply = post("job-result", {**who, "result": {
        "version": 1, "job_id": job_id, "uci": "e2e4",
    }})
    assert status == 409 and "error" in reply
    assert post("ready", {**who, "model_sha256": MODEL_SHA})[0] == 400
    with pytest.raises(ValueError):
        arm_one_job(path, WORKER)
    with pytest.raises(ValueError):
        finalize_one_result(path, WORKER)

    # STOP request alone neither confirms stopping nor advances chess state.
    assert tournament_checkpoint.load(path, SANDBOX_TOURNAMENT_ID) == old_checkpoint
    with sqlite3.connect(path) as db:
        assert db.execute(
            "SELECT job_id,phase,acknowledged_at FROM worker_update_pause_requests "
            "WHERE worker_id=?", (WORKER,)
        ).fetchone() == (job_id, "requested", None)
        assert db.execute(
            "SELECT state,job_id FROM worker_job_safety WHERE worker_id=?",
            (WORKER,)
        ).fetchone() == ("running", job_id)
        assert db.execute(
            "SELECT COUNT(*) FROM worker_inference_results WHERE worker_id=?",
            (WORKER,)
        ).fetchone()[0] == 0


def test_power_loss_reconnect_never_recovers_running_job_from_ready(sandbox):
    """Issue #291: a phone reboot is not evidence that a previous lease stopped."""
    from remote_worker_update_pause import WorkerUpdatePause

    path, token, post = sandbox
    who = auth(token)
    assert post("ready", {**who, "model_sha256": MODEL_SHA})[1]["state"] == "ready"
    job_id = arm_one_job(path, WORKER)
    checkpoint_before = tournament_checkpoint.load(path, SANDBOX_TOURNAMENT_ID)

    # Simulate loss of the client's in-memory session and a durable STOP request
    # remaining in SQLite while the phone reconnects with its valid token.
    req_id = WorkerUpdatePause(path).request(WORKER, job_id)
    reopened = WorkerUpdatePause(path)
    assert reopened.pending(WORKER) == {
        "job_id": job_id, "request_id": req_id, "phase": "requested"
    }
    assert post("ready", {**who, "model_sha256": MODEL_SHA})[0] == 400
    assert post("job-poll", who)[1]["state"] == "blocked"
    assert post("update-pause-poll", who)[1]["instruction"] == {
        "action": "stop_checkpoint", "job_id": job_id, "request_id": req_id
    }
    # Neither reboot nor reconnect can release the old lease or change chess.
    with sqlite3.connect(path) as db:
        assert db.execute(
            "SELECT state,job_id FROM worker_job_safety WHERE worker_id=?",
            (WORKER,),
        ).fetchone() == ("running", job_id)
        assert db.execute(
            "SELECT phase,acknowledged_at FROM worker_update_pause_requests "
            "WHERE worker_id=?", (WORKER,),
        ).fetchone() == ("requested", None)
    assert tournament_checkpoint.load(path, SANDBOX_TOURNAMENT_ID) == checkpoint_before


def test_stop_ack_must_not_be_inferred_from_reconnect_or_wrong_proof(sandbox):
    """Issue #291: no STOP ACK from merely receiving STOP or asserting ready."""
    from remote_worker_update_pause import WorkerUpdatePause

    path, token, post = sandbox
    who = auth(token)
    assert post("ready", {**who, "model_sha256": MODEL_SHA})[1]["state"] == "ready"
    job_id = arm_one_job(path, WORKER)
    stop = WorkerUpdatePause(path)
    request_id = stop.request(WORKER, job_id)
    old = tournament_checkpoint.load(path, SANDBOX_TOURNAMENT_ID)
    cases = [
        (False, False, request_id),
        (True, False, request_id),
        (False, True, request_id),
        (True, True, "0" * 32),
    ]
    for stopped, checkpoint_verified, received_id in cases:
        with pytest.raises(ValueError):
            stop.acknowledge(
                WORKER, job_id, received_id,
                worker_stopped=stopped,
                checkpoint_verified=checkpoint_verified,
            )
    with sqlite3.connect(path) as db:
        assert db.execute(
            "SELECT phase,acknowledged_at FROM worker_update_pause_requests "
            "WHERE worker_id=?", (WORKER,),
        ).fetchone() == ("requested", None)
        assert db.execute(
            "SELECT state,job_id FROM worker_job_safety WHERE worker_id=?",
            (WORKER,),
        ).fetchone() == ("running", job_id)
    assert post("job-result", {**who, "result": {
        "version": 1, "job_id": job_id, "uci": "e2e4"
    }})[0] == 409
    assert tournament_checkpoint.load(path, SANDBOX_TOURNAMENT_ID) == old


def test_abrupt_power_loss_without_stop_does_not_reassign_or_recover(sandbox):
    """A reconnect to a running job is not a successful STOP/ACK handshake."""
    path, token, post = sandbox
    who = auth(token)
    assert post("ready", {**who, "model_sha256": MODEL_SHA})[1]["state"] == "ready"
    job_id = arm_one_job(path, WORKER)
    original = tournament_checkpoint.load(path, SANDBOX_TOURNAMENT_ID)

    # Simulated process death: no STOP was requested or acknowledged.
    # Valid old credentials on reconnect must not steal the running lease.
    assert post("ready", {**who, "model_sha256": MODEL_SHA})[1]["state"] == "blocked"
    code, job = post("job-poll", who)
    assert code == 200 and job["state"] == "assigned"
    assert job["job"]["job_id"] == job_id
    assert post("update-pause-poll", who)[1]["instruction"] == {"action": "none"}
    with pytest.raises(ValueError, match="idle"):
        arm_one_job(path, WORKER)

    # There is no new checkpoint or false idle transition.
    assert tournament_checkpoint.load(path, SANDBOX_TOURNAMENT_ID) == original
    with sqlite3.connect(path) as db:
        assert db.execute(
            "SELECT state,job_id FROM worker_job_safety WHERE worker_id=?",
            (WORKER,)
        ).fetchone() == ("running", job_id)
        assert db.execute(
            "SELECT COUNT(*) FROM worker_update_pause_requests WHERE worker_id=?",
            (WORKER,)
        ).fetchone()[0] == 0


def test_trusted_ack_does_not_release_execution_lease_or_allow_replay(sandbox):
    """ACK records a verified stop, but is NOT a new idle lease or move commit."""
    from remote_worker_update_pause import WorkerUpdatePause

    path, token, post = sandbox
    who = auth(token)
    assert post("ready", {**who, "model_sha256": MODEL_SHA})[1]["state"] == "ready"
    job_id = arm_one_job(path, WORKER)
    before = tournament_checkpoint.load(path, SANDBOX_TOURNAMENT_ID)
    pause = WorkerUpdatePause(path)
    request_id = pause.request(WORKER, job_id)

    # Only a trusted coordinator is allowed to call this method AFTER independent
    # validation. The worker HTTP API exposes no route for asserting this proof.
    pause.acknowledge(
        WORKER, job_id, request_id,
        worker_stopped=True, checkpoint_verified=True,
    )
    assert pause.poll_stop_instruction(WORKER) == {
        "action": "await_reconciliation"
    }
    assert post("update-pause-poll", who)[1]["instruction"] == {
        "action": "await_reconciliation"
    }
    with pytest.raises(ValueError, match="Stale pause"):
        pause.acknowledge(
            WORKER, job_id, request_id,
            worker_stopped=True, checkpoint_verified=True,
        )
    assert post("job-poll", who)[1]["state"] == "blocked"
    assert post("job-result", {**who, "result": {
        "version": 1, "job_id": job_id, "uci": "e2e4"
    }})[0] == 409
    with pytest.raises(ValueError):
        arm_one_job(path, WORKER)
    with pytest.raises(ValueError):
        finalize_one_result(path, WORKER)

    with sqlite3.connect(path) as db:
        phase, ack_at = db.execute(
            "SELECT phase,acknowledged_at FROM worker_update_pause_requests "
            "WHERE worker_id=?", (WORKER,),
        ).fetchone()
        assert phase == "acknowledged" and ack_at is not None
        assert db.execute(
            "SELECT state,job_id FROM worker_job_safety WHERE worker_id=?",
            (WORKER,),
        ).fetchone() == ("running", job_id)
        assert db.execute(
            "SELECT COUNT(*) FROM worker_inference_results WHERE worker_id=?",
            (WORKER,),
        ).fetchone()[0] == 0
    assert tournament_checkpoint.load(path, SANDBOX_TOURNAMENT_ID) == before


def test_stop_fences_coordinator_read_even_for_result_received_before_stop(sandbox):
    """A previously stored legal UCI must not be handed to the runner after STOP."""
    from remote_worker_update_pause import WorkerUpdatePause

    path, token, post = sandbox
    who = auth(token)
    assert post("ready", {**who, "model_sha256": MODEL_SHA})[1]["state"] == "ready"
    job_id = arm_one_job(path, WORKER)
    original = tournament_checkpoint.load(path, SANDBOX_TOURNAMENT_ID)
    response_code, accepted = post("job-result", {**who, "result": {
        "version": 1, "job_id": job_id, "uci": "e2e4"
    }})
    assert response_code == 200 and accepted["state"] == "received_not_applied"
    delivery = WorkerInferenceDelivery(path)
    assert delivery.read_arena_result(WORKER, job_id) == "e2e4"

    # STOP wins over an uncommitted result, including coordinator-side reads.
    stop = WorkerUpdatePause(path)
    request_id = stop.request(WORKER, job_id)
    with pytest.raises(ValueError, match="fenced"):
        delivery.read_arena_result(WORKER, job_id)
    with pytest.raises(ValueError):
        finalize_one_result(path, WORKER)
    stop.acknowledge(WORKER, job_id, request_id,
                     worker_stopped=True, checkpoint_verified=True)
    with pytest.raises(ValueError, match="fenced"):
        delivery.read_arena_result(WORKER, job_id)
    assert tournament_checkpoint.load(path, SANDBOX_TOURNAMENT_ID) == original
    with sqlite3.connect(path) as db:
        assert db.execute(
            "SELECT state,job_id FROM worker_job_safety WHERE worker_id=?",
            (WORKER,),
        ).fetchone() == ("running", job_id)
        assert db.execute(
            "SELECT result_json FROM worker_inference_results WHERE worker_id=?",
            (WORKER,),
        ).fetchone() is not None  # still durable, but fenced from chess


def test_opt_in_fenced_job_rejects_stale_lease_and_protocol_downgrade(sandbox):
    """A new-generation assignment can never accept an old job's UCI receipt."""
    import secrets

    path, token, post = sandbox
    who = auth(token)
    assert post("ready", {**who, "model_sha256": MODEL_SHA})[1]["state"] == "ready"
    lease_token = secrets.token_hex(32)
    job_id = "fenced_" + secrets.token_hex(12)
    job = {
        "version": 3, "job_id": job_id, "fen": chess.STARTING_FEN,
        "model_sha256": MODEL_SHA,
        "legal_moves": sorted(move.uci() for move in chess.Board().legal_moves),
        "max_tokens": 32, "timeout_seconds": 600,
        "lease_token": lease_token,
    }
    delivery = WorkerInferenceDelivery(path)
    assert delivery.assign_from_arena(WORKER, job) == job_id
    code, poll = post("job-poll", who)
    assert code == 200 and poll["job"] == job
    original = tournament_checkpoint.load(path, SANDBOX_TOURNAMENT_ID)

    def result(version, **fields):
        return post("job-result", {**who, "result": {
            "version": version, "job_id": job_id, "uci": "e2e4", **fields,
        }})[0]

    assert result(1) == 409  # No old-protocol bypass.
    assert result(2, lease_token="0" * 64) == 409
    assert result(2, lease_token=lease_token[:-1] + "g") == 409
    assert result(2, lease_token=lease_token) == 200
    assert result(2, lease_token=lease_token) == 200  # idempotent receipt
    assert delivery.read_arena_result(WORKER, job_id) == "e2e4"
    assert tournament_checkpoint.load(path, SANDBOX_TOURNAMENT_ID) == original
    assert finalize_one_result(path, WORKER) == "e2e4"
    with pytest.raises(ValueError):
        delivery.submit_inference_result(WORKER, {
            "version": 2, "job_id": job_id, "uci": "e2e4",
            "lease_token": lease_token,
        })


def test_fenced_job_still_rejects_correct_capability_after_stop(sandbox):
    """A matching v3 lease capability never overrides the durable STOP gate."""
    import secrets
    from remote_worker_update_pause import WorkerUpdatePause

    path, token, post = sandbox
    who = auth(token)
    assert post("ready", {**who, "model_sha256": MODEL_SHA})[1]["state"] == "ready"
    job_id = "fenced_" + secrets.token_hex(12)
    job = {
        "version": 3, "job_id": job_id, "fen": chess.STARTING_FEN,
        "model_sha256": MODEL_SHA,
        "legal_moves": sorted(move.uci() for move in chess.Board().legal_moves),
        "max_tokens": 32, "timeout_seconds": 600,
        "lease_token": secrets.token_hex(32),
    }
    delivery = WorkerInferenceDelivery(path)
    delivery.assign_from_arena(WORKER, job)
    original = tournament_checkpoint.load(path, SANDBOX_TOURNAMENT_ID)
    request_id = WorkerUpdatePause(path).request(WORKER, job_id)
    assert post("job-result", {**who, "result": {
        "version": 2, "job_id": job_id, "uci": "e2e4",
        "lease_token": job["lease_token"],
    }})[0] == 409
    assert post("job-poll", who)[1]["state"] == "blocked"
    assert post("update-pause-poll", who)[1]["instruction"] == {
        "action": "stop_checkpoint", "job_id": job_id, "request_id": request_id,
        "lease_token_sha256": hashlib.sha256(
            job["lease_token"].encode("ascii")
        ).hexdigest(),
    }
    # Retrying STOP never reveals the bearer lease token.
    instruction = post("update-pause-poll", who)[1]["instruction"]
    assert instruction["lease_token_sha256"] != job["lease_token"]
    assert instruction["request_id"] == request_id
    assert tournament_checkpoint.load(path, SANDBOX_TOURNAMENT_ID) == original


def test_coordinator_stop_retry_reuses_request_without_faking_ack(sandbox):
    """Lost STOP response can be retried without minting a new STOP generation."""
    from remote_worker_update_pause import WorkerUpdatePause

    path, token, post = sandbox
    who = auth(token)
    assert post("ready", {**who, "model_sha256": MODEL_SHA})[1]["state"] == "ready"
    job_id = arm_one_job(path, WORKER)
    first = WorkerUpdatePause(path).request(WORKER, job_id)
    # Independent coordinator instance simulates a restart after losing reply.
    retry = WorkerUpdatePause(path)
    assert retry.request(WORKER, job_id) == first
    assert retry.request(WORKER, job_id) == first
    with sqlite3.connect(path) as db:
        assert db.execute(
            "SELECT COUNT(*) FROM worker_update_pause_requests WHERE worker_id=?",
            (WORKER,),
        ).fetchone()[0] == 1
        assert db.execute(
            "SELECT phase,acknowledged_at FROM worker_update_pause_requests "
            "WHERE worker_id=?", (WORKER,),
        ).fetchone() == ("requested", None)
        assert db.execute(
            "SELECT state,job_id FROM worker_job_safety WHERE worker_id=?",
            (WORKER,),
        ).fetchone() == ("running", job_id)
    for _ in range(2):
        assert post("update-pause-poll", who)[1]["instruction"] == {
            "action": "stop_checkpoint", "job_id": job_id, "request_id": first,
        }
    with pytest.raises(ValueError):
        retry.request(WORKER, "another_job_123456")
    retry.acknowledge(WORKER, job_id, first,
                      worker_stopped=True, checkpoint_verified=True)
    with pytest.raises(ValueError, match="another state"):
        retry.request(WORKER, job_id)
    assert post("job-poll", who)[1]["state"] == "blocked"
