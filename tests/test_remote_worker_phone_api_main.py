"""Integration of a flag-gated Android gateway with the ordinary Arena Handler.

Every test uses loopback with a dynamic port, a temporary SQLite file, and
temporary Worker credentials; none uses the live Arena or its secret token.
"""
import json
import os
import sqlite3
import threading
from http.server import ThreadingHTTPServer
from urllib import request, error

import chess
import pytest
import server
from remote_worker_inference_delivery import WorkerInferenceDelivery
from remote_worker_tokens import WorkerTokenStore

WORKER = "android_test_12345678"
NONCE = "0123456789abcdef0123456789abcdef"
SHA = "a" * 64


@pytest.fixture
def gateway(tmp_path, monkeypatch):
    path = tmp_path / "arena.sqlite3"
    monkeypatch.setattr(server, "DB", path)
    from remote_worker_rate_policy import WorkerRatePolicy
    monkeypatch.setattr(server, "_worker_rate_policy", WorkerRatePolicy())
    monkeypatch.setenv("ARENA_PHONE_INFERENCE_ENABLED", "0")
    monkeypatch.delenv("ARENA_PHONE_WORKER_API_ENABLED", raising=False)
    token = WorkerTokenStore(path).issue(WORKER, ttl_seconds=600)
    http = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()

    def post(route, data, *, content_type="application/json", headers=None):
        req = request.Request(
            f"http://127.0.0.1:{http.server_port}/api/remote-workers/{route}",
            json.dumps(data).encode(),
            {"Content-Type": content_type, **(headers or {})}, method="POST"
        )
        try:
            with request.urlopen(req, timeout=4) as resp:
                return resp.status, json.load(resp)
        except error.HTTPError as exc:
            return exc.code, json.load(exc)
    try:
        yield path, token, post
    finally:
        http.shutdown()
        http.server_close()
        thread.join(timeout=4)


def credentials(token):
    return {"worker_id": WORKER, "token": token, "nonce": NONCE}


def test_disabled_by_default_never_uses_web_login(gateway):
    _, token, post = gateway
    for route in ("ready", "job-poll", "job-result", "board-view", "update-pause-poll", "model-source"):
        code, body = post(route, credentials(token))
        assert code == 503
        assert "login_url" not in body
    assert os.environ.get("ARENA_PHONE_INFERENCE_ENABLED") == "0"


def test_bad_machine_auth_and_nonce_never_leak_state(gateway, monkeypatch):
    _, token, post = gateway
    monkeypatch.setenv("ARENA_PHONE_WORKER_API_ENABLED", "1")
    for route in ("ready", "job-poll", "job-result", "board-view", "update-pause-poll", "model-source"):
        body = credentials("definitely-wrong-token-000000000000000000000000")
        if route == "ready":
            body["model_sha256"] = SHA
        elif route == "job-result":
            body["result"] = {"version": 1, "job_id": "game_test_12345678", "uci": "e2e4"}
        code, body = post(route, body)
        assert code == 401
        assert "worker_id" not in body and "nonce" not in body
    for request_body in (
        {**credentials(token), "nonce": "invalid"},
        {**credentials(token), "nonce": "A"*32},
        {**credentials(token), "unexpected": 1},
    ):
        code, body = post("job-poll", request_body)
        assert code == 400 and "nonce" not in body
    assert post("job-poll", credentials(token), content_type="text/plain")[0] == 415


def test_v2_job_ready_result_http_roundtrip_without_lease_completion(gateway, monkeypatch):
    path, token, post = gateway
    monkeypatch.setenv("ARENA_PHONE_WORKER_API_ENABLED", "1")
    auth = credentials(token)
    code, data = post("job-poll", auth)
    assert code == 200 and data["state"] == "none"
    assert data["source"] == "coordinator" and data["nonce"] == NONCE
    code, data = post("ready", {**auth, "model_sha256": SHA})
    assert code == 200 and data["state"] == "ready"
    code, data = post("update-pause-poll", auth)
    assert code == 200 and data["instruction"] == {"action": "none"}
    assert data["install_allowed"] is False
    code, data = post("board-view", auth)
    assert code == 200 and data["active"] is False

    # Trusted in-process coordinator method, never an HTTP Worker endpoint.
    delivery = WorkerInferenceDelivery(path)
    job = {
        "version": 2, "job_id": "game_test_12345678",
        "fen": chess.STARTING_FEN, "model_sha256": SHA,
        "legal_moves": sorted(move.uci() for move in chess.Board().legal_moves),
        "max_tokens": 32, "timeout_seconds": 90,
    }
    delivery.assign_from_arena(WORKER, job)
    code, data = post("job-poll", auth)
    assert code == 200 and data["state"] == "assigned" and data["job"] == job
    for move in ("e2e5", "e9e4"):
        code, data = post("job-result", {
            **auth, "result": {"version": 1, "job_id": job["job_id"], "uci": move}
        })
        assert code == 409 and "nonce" not in data
    code, data = post("job-result", {
        **auth, "result": {"version": 1, "job_id": job["job_id"], "uci": "e2e4"}
    })
    assert code == 200 and data["state"] == "received_not_applied"
    code, data = post("job-poll", auth)
    assert code == 200 and data["state"] == "awaiting_arena" and "job" not in data
    with sqlite3.connect(path) as db:
        assert db.execute(
            "SELECT state,job_id FROM worker_job_safety WHERE worker_id=?", (WORKER,)
        ).fetchone() == ("running", job["job_id"])
        assert db.execute(
            "SELECT name FROM sqlite_master WHERE name='tournament_checkpoints'"
        ).fetchone() is None


def test_hold_blocks_ready_and_poll(gateway, monkeypatch):
    path, token, post = gateway
    monkeypatch.setenv("ARENA_PHONE_WORKER_API_ENABLED", "1")
    auth = credentials(token)
    assert post("ready", {**auth, "model_sha256": SHA})[1]["state"] == "ready"
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO worker_update_holds VALUES (?,?,?,?)",
                   (WORKER, "b" * 64, 1000.0, 1300.0))
    assert post("job-poll", auth)[1]["state"] == "blocked"
    # Active update hold fails closed; readiness cannot override it.
    assert post("ready", {**auth, "model_sha256": SHA})[0] == 400
    assert post("update-pause-poll", auth)[1]["instruction"] == {"action": "none"}


def test_gateway_never_exposes_assignment_completion_or_installer():
    from remote_worker_phone_api import ROUTES
    assert not any(route.endswith(s) for route in ROUTES for s in (
        "/assign", "/complete", "/update-release", "/ack", "/update-reserve"
    ))


def test_elimination_assignment_requires_explicit_v3_readiness(gateway, monkeypatch):
    from elimination_chess import EliminationBoard
    monkeypatch.setenv('ARENA_PHONE_WORKER_API_ENABLED', '1')
    path, token, post = gateway
    auth = credentials(token)
    board = EliminationBoard('4k3/8/8/8/8/8/4R3/4K3 w - - 0 1')
    job = dict(version=3, variant='elimination', job_id='game_variant_12345678',
               fen=board.fen(), model_sha256=SHA, max_tokens=32, timeout_seconds=90,
               legal_moves=sorted(m.uci() for m in board.legal_moves))
    delivery = WorkerInferenceDelivery(path)
    assert post('ready', {**auth, 'model_sha256': SHA})[0] == 200
    assert delivery.find_ready_worker(SHA, 3) is None
    with pytest.raises(ValueError, match='protocol'):
        delivery.assign_from_arena(WORKER, job)
    for invalid in (True, 5, '3', None):
        assert post('ready', {**auth, 'model_sha256': SHA, 'protocol_version': invalid})[0] == 400
    assert post('ready', {**auth, 'model_sha256': SHA, 'protocol_version': 3})[0] == 200
    assert delivery.find_ready_worker(SHA, 3) == WORKER
    delivery.assign_from_arena(WORKER, job)
    code, body = post('job-poll', auth)
    assert code == 200 and body['job'] == job
    assert {'e2e7', 'e2e8'} <= set(body['job']['legal_moves'])
    code, body = post('job-result', {**auth, 'result': {'version': 1, 'job_id': job['job_id'], 'uci': 'e2e7'}})
    assert code == 200 and body['state'] == 'received_not_applied'

def test_model_source_auth_assignment_and_rate_limit(gateway, monkeypatch):
    path, token, post = gateway
    monkeypatch.setenv("ARENA_PHONE_WORKER_API_ENABLED", "1")
    auth = credentials(token)
    assert post("model-source", {**auth, "sha256": SHA})[0] == 400
    assert post("model-source", auth)[0] == 409
    delivery = WorkerInferenceDelivery(path)
    delivery.register_local_ready(WORKER, SHA, 4)
    job = dict(version=4, job_id="game_test_12345678", fen=chess.STARTING_FEN,
               model_sha256=SHA, legal_moves=sorted(m.uci() for m in chess.Board().legal_moves),
               variant="standard", max_tokens=32, timeout_seconds=90)
    delivery.assign_from_arena(WORKER, job)
    approved = dict(model_id=1, version_id=2, sha256=SHA, size_bytes=4096,
                    source_url="https://huggingface.co/a/b/resolve/"+"1"*40+"/model.gguf")
    import remote_worker_model_registry
    monkeypatch.setattr(remote_worker_model_registry, "resolve_approved_worker_gguf",
                        lambda db, digest: approved if digest == SHA else None)
    code, body = post("model-source", auth)
    assert code == 200 and body["model"] == approved
    assert body["nonce"] == NONCE and body["state"] == "approved_metadata_only"
    assert "lease_token" not in body
    for _ in range(10):
        assert post("model-source", auth)[0] == 200
    assert post("model-source", auth)[0] == 429

def test_machine_authentication_has_one_route_and_no_browser_session_fallback(gateway, monkeypatch):
    from pathlib import Path
    source = Path(server.__file__).read_text()
    dispatch = source.split("    def do_POST(self):", 1)[1]
    auth_route = "if path == '/api/remote-workers/authenticate':"
    assert dispatch.count(auth_route) == 1
    assert dispatch.index(auth_route) < dispatch.index("if path == '/api/auth/login':")
    _, token, post = gateway
    # Even a successfully authenticated browser session must not authorize
    # machine-only routes. The machine token remains independently mandatory.
    monkeypatch.setattr(server.Handler, "authenticated_user", lambda self: "admin")
    monkeypatch.setenv("ARENA_PHONE_WORKER_API_ENABLED", "1")
    browser = {"Cookie": "arena_session=admin"}
    code, body = post("authenticate", {"worker_id": WORKER, "token": "wrong-token-" + "x"*40}, headers=browser)
    assert code == 401 and body == {"authenticated": False}
    code, body = post("authenticate", {"worker_id": WORKER, "token": token}, headers=browser)
    assert code == 200 and body == {"authenticated": True, "worker_id": WORKER}
    code, body = post("model-source", credentials("wrong-token-" + "x"*40), headers=browser)
    assert code == 401 and "model" not in body and "nonce" not in body
    code, body = post("model-source", credentials(token), headers=browser)
    assert code == 409 and "model" not in body
