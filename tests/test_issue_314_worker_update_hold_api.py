"""Machine-only Worker update hold API, with real SQLite transaction regressions."""
import io
import json
import sqlite3
import time

import pytest

import worker_backend
from remote_worker_jobs import DurableWorkerJobs
from remote_worker_tokens import WorkerTokenStore
from worker_update_hold_api import (
    ROUTES, UPDATE_RESERVE_ROUTE, UPDATE_RELEASE_ROUTE,
    UPDATE_HOLD_STATUS_ROUTE, handle_worker_update_hold,
)

WORKER = "android_test_12345678"
NONCE = "0123456789abcdef0123456789abcdef"
CAPABILITY = "a" * 64


class Reply:
    def __init__(self, payload, *, cookie=True):
        raw = json.dumps(payload).encode("utf-8") if not isinstance(payload, bytes) else payload
        self.headers = {"Content-Length": str(len(raw)), "Content-Type": "application/json"}
        if cookie:
            self.headers["Cookie"] = "arena_session=admin"
        self.rfile = io.BytesIO(raw)
        self.responses = []

    def send_json(self, data, status=200, *_):
        self.responses.append((status, data))


@pytest.fixture
def env(tmp_path):
    db = tmp_path / "worker.sqlite3"
    token = WorkerTokenStore(db).issue(WORKER)
    jobs = DurableWorkerJobs(db)
    return db, token, jobs


def payload(token, hold=CAPABILITY, *, nonce=NONCE):
    return {"worker_id": WORKER, "token": token, "nonce": nonce, "hold_token": hold}


def post(env, path, request, *, cookie=True):
    db, _, _ = env
    h = Reply(request, cookie=cookie)
    assert worker_backend.dispatch_worker_post(h, path, db, {})
    assert len(h.responses) == 1
    return h.responses[0]


def set_idle(env, *, seconds_ago=0):
    db, _, _ = env
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO worker_job_safety VALUES (?,NULL,'idle_confirmed',NULL,?)",
            (WORKER, time.time() - seconds_ago),
        )


def test_reservation_routes_are_machine_only_and_explicit():
    assert ROUTES == {UPDATE_RESERVE_ROUTE, UPDATE_RELEASE_ROUTE, UPDATE_HOLD_STATUS_ROUTE}
    assert ROUTES <= worker_backend.WORKER_POST_ROUTES
    assert not handle_worker_update_hold(Reply(b"{}"), "/api/models", ":memory:")


def test_cookie_never_replaces_token(env):
    db, token, jobs = env
    set_idle(env)
    for route in ROUTES:
        status, body = post(env, route, payload("incorrect-machine-token"))
        assert status == 401
        assert "hold_token" not in body
        assert "state" not in body
    assert not jobs.validate_update_hold(WORKER, CAPABILITY)


def test_atomically_reserve_then_release_and_block_assignments(env):
    db, token, jobs = env
    set_idle(env)
    status, reserved = post(env, UPDATE_RESERVE_ROUTE, payload(token))
    assert status == 200
    assert reserved["state"] == "reserved"
    assert reserved["hold_token"] == CAPABILITY
    assert reserved["install_allowed"] is False
    assert reserved["source"] == "coordinator"
    assert jobs.validate_update_hold(WORKER, CAPABILITY)
    with pytest.raises(ValueError, match="reserved"):
        jobs.assign(WORKER, "job_12345678", timeout_seconds=30)
    status, duplicate = post(env, UPDATE_RESERVE_ROUTE, payload(token, "b" * 64))
    assert status == 409
    assert "hold_token" not in duplicate
    assert jobs.validate_update_hold(WORKER, CAPABILITY)
    status, forged = post(env, UPDATE_RELEASE_ROUTE, payload(token, "c" * 64))
    assert status == 409
    assert forged.get("released") is not True
    assert jobs.validate_update_hold(WORKER, CAPABILITY)
    status, released = post(env, UPDATE_RELEASE_ROUTE, payload(token))
    assert status == 200
    assert released["state"] == "unknown"
    assert released["released"] is True
    assert released["install_allowed"] is False
    assert not jobs.validate_update_hold(WORKER, CAPABILITY)
    assert jobs.state(WORKER) == "unknown"
    with pytest.raises(ValueError):
        jobs.assign(WORKER, "job_12345678", timeout_seconds=30)


def test_absent_and_stale_idle_never_reserve(env):
    db, token, jobs = env
    assert post(env, UPDATE_RESERVE_ROUTE, payload(token))[0] == 409
    set_idle(env, seconds_ago=45)
    assert post(env, UPDATE_RESERVE_ROUTE, payload(token))[0] == 409
    assert not jobs.validate_update_hold(WORKER, CAPABILITY)


def test_status_fails_closed_on_wrong_capability_and_expiry(env):
    db, token, jobs = env
    set_idle(env)
    assert post(env, UPDATE_RESERVE_ROUTE, payload(token))[0] == 200
    status, view = post(env, UPDATE_HOLD_STATUS_ROUTE, payload(token))
    assert status == 200 and view["state"] == "active"
    assert "hold_token" not in view
    assert view["install_allowed"] is False
    assert post(env, UPDATE_HOLD_STATUS_ROUTE, payload(token, "b"*64))[0] == 409
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE worker_update_holds SET expires_at=? WHERE worker_id=?",
                     (time.time()-1, WORKER))
    status, expired = post(env, UPDATE_HOLD_STATUS_ROUTE, payload(token))
    assert status == 200 and expired["state"] == "expired"
    assert expired["install_allowed"] is False
    with pytest.raises(ValueError, match="reserved"):
        jobs.assign(WORKER, "job_12345678", timeout_seconds=30)
    assert post(env, UPDATE_RELEASE_ROUTE, payload(token))[0] == 200
    status, absent = post(env, UPDATE_HOLD_STATUS_ROUTE, payload(token))
    assert status == 200 and absent["state"] == "absent"


@pytest.mark.parametrize("hold", [None, "", "x"*64, "A"*64, "0"*63])
def test_reserve_requires_prejournaled_capability(env, hold):
    db, token, jobs = env
    set_idle(env)
    status, reply = post(env, UPDATE_RESERVE_ROUTE, payload(token, hold))
    assert status == 409 and "hold_token" not in reply
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT count(*) FROM worker_update_holds").fetchone()[0] == 0


def test_malformed_request_rejected_without_state_changes(env):
    db, token, jobs = env
    set_idle(env)
    for raw in (b"{", b"[]", b"x"*2049,):
        status, reply = post(env, UPDATE_RESERVE_ROUTE, raw)
        assert status in (400, 409)
        assert "hold_token" not in reply
    status, reply = post(env, UPDATE_RESERVE_ROUTE, payload(token, nonce="F"*32))
    assert status == 409 and "hold_token" not in reply
    assert not jobs.validate_update_hold(WORKER, CAPABILITY)


def test_unauthorized_release_preserves_hold_after_expiry(env):
    db, token, jobs = env
    set_idle(env)
    assert post(env, UPDATE_RESERVE_ROUTE, payload(token))[0] == 200
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE worker_update_holds SET expires_at=? WHERE worker_id=?",
                     (time.time()-5, WORKER))
    assert post(env, UPDATE_RELEASE_ROUTE, payload("incorrect-token"))[0] == 401
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT hold_token FROM worker_update_holds").fetchone()[0] == CAPABILITY
    with pytest.raises(ValueError, match="reserved"):
        jobs.assign(WORKER, "job_12345678", timeout_seconds=30)
