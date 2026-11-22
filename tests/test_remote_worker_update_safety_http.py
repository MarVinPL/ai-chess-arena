"""HTTP integration tests for authenticated nonce-bound Worker update safety.

These tests use an ephemeral loopback server and temporary SQLite database,
never a deployed coordinator. The API must never authorize installation.
"""
import json
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

import remote_worker_tokens
import server


WORKER_ID = "worker_12345678"
NONCE = "00112233445566778899aabbccddeeff"


@pytest.fixture()
def endpoint(tmp_path, monkeypatch):
    db = tmp_path / "worker-safety.sqlite3"
    monkeypatch.setattr(server, "DB", db)
    token = remote_worker_tokens.WorkerTokenStore(db).issue(WORKER_ID, ttl_seconds=3600)
    http = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    try:
        def post(payload):
            data = json.dumps(payload).encode("utf-8")
            request = urllib.request.Request(
                f"http://127.0.0.1:{http.server_port}/api/remote-workers/update-safety",
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=4) as result:
                    return result.status, json.loads(result.read())
            except urllib.error.HTTPError as error:
                return error.code, json.loads(error.read())

        yield db, token, post
    finally:
        http.shutdown()
        http.server_close()
        thread.join(timeout=5)


def request_data(token, *, nonce=NONCE):
    return {"worker_id": WORKER_ID, "token": token, "nonce": nonce}


def test_no_job_row_is_unknown_and_never_grants_install(endpoint):
    _, token, post = endpoint
    status, response = post(request_data(token))
    assert status == 200
    assert response["state"] == "unknown"
    assert response["authenticated"] is True
    assert response["source"] == "coordinator"
    assert response["worker_id"] == WORKER_ID
    assert response["nonce"] == NONCE
    assert response["active_tasks"] is None
    assert abs(response["checked_at_unix_ms"] - int(time.time() * 1000)) < 5000
    assert response["install_allowed"] is False


def test_recent_real_terminal_state_only_can_report_idle(endpoint):
    db, token, post = endpoint
    now = time.time()
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO worker_job_safety VALUES (?,NULL,'idle_confirmed',NULL,?)",
            (WORKER_ID, now),
        )
    status, response = post(request_data(token))
    assert status == 200
    assert response["state"] == "idle_confirmed"
    assert response["active_tasks"] == 0
    assert response["install_allowed"] is False
    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE worker_job_safety SET updated_at=? WHERE worker_id=?",
            (now - 31, WORKER_ID),
        )
    status, response = post(request_data(token))
    assert status == 200
    assert response["state"] == "unknown"
    assert response["active_tasks"] is None
    assert response["install_allowed"] is False


def test_running_or_expired_job_is_never_idle(endpoint):
    db, token, post = endpoint
    now = time.time()
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO worker_job_safety VALUES (?,?,'running',?,?)",
            (WORKER_ID, "job_12345678", now + 60, now),
        )
    status, response = post(request_data(token))
    assert status == 200
    assert response["state"] == "running"
    assert response["active_tasks"] == 1
    assert response["install_allowed"] is False
    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE worker_job_safety SET deadline=? WHERE worker_id=?",
            (now - 1, WORKER_ID),
        )
    _, response = post(request_data(token))
    assert response["state"] == "unknown"
    assert response["active_tasks"] is None


def test_nonce_is_validated_and_not_reflected_on_auth_failure(endpoint):
    _, token, post = endpoint
    for wrong in ["bad", "A" * 32, "", "f" * 33, None]:
        status, response = post(request_data(token, nonce=wrong))
        assert status == 400
        assert "nonce" not in response
    status, response = post(request_data("incorrect-token"))
    assert status == 401
    assert "nonce" not in response
    assert "authenticated" not in response
    alternate = "a" * 32
    status, response = post(request_data(token, nonce=alternate))
    assert status == 200
    assert response["nonce"] == alternate
    assert response["install_allowed"] is False
