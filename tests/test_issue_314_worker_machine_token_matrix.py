"""Security matrix for the consolidated Worker HTTP namespace, Issue #314.

An administrator's browser cookie is never an alternative to machine token.
All requests operate on a disposable SQLite database.
"""
import io
import json

import pytest

from worker_backend import WORKER_POST_ROUTES, dispatch_worker_post
from remote_worker_phone_api import ROUTES as PHONE_ROUTES


class Reply:
    def __init__(self, payload):
        raw = json.dumps(payload).encode("utf-8")
        self.headers = {
            "Content-Length": str(len(raw)),
            "Content-Type": "application/json",
            "Cookie": "arena_session=admin",
        }
        self.rfile = io.BytesIO(raw)
        self.responses = []

    def send_json(self, value, status=200, *_):
        self.responses.append((status, value))


@pytest.mark.parametrize("route", sorted(WORKER_POST_ROUTES))
def test_admin_cookie_does_not_authorize_any_worker_post(
    route, tmp_path, monkeypatch,
):
    # Enable opt-in phone gateway for this test; otherwise its disabled state
    # would return 503 before the authenticator is consulted.
    monkeypatch.setenv("ARENA_PHONE_WORKER_API_ENABLED", "1")
    payload = {
        "worker_id": "android_test_12345678",
        "token": "invalid-machine-token",
        "nonce": "a" * 32,
    }
    if route.endswith("/ready"):
        payload["model_sha256"] = "b" * 64
    elif route.endswith("/job-result"):
        payload["result"] = {"version": 1, "job_id": "game_12345678", "uci": "e2e4"}
    elif route.endswith(("/update-reserve", "/update-release", "/update-hold-status")):
        payload["hold_token"] = "c" * 64
    elif route.endswith("/native-stop-report"):
        payload.update(job_id="game_12345678", request_id="d" * 32, lease_token="e" * 64)
    elif route.endswith("/resume-checkpoint"):
        payload.update(job_id="game_12345678", request_id="d" * 32, checkpoint_sha256="e" * 64)
    elif route.endswith("/authenticate"):
        del payload["nonce"]
    handler = Reply(payload)
    assert dispatch_worker_post(handler, route, tmp_path / "worker.sqlite", {})
    assert len(handler.responses) == 1
    code, response = handler.responses[0]
    assert code == 401, (route, code, response)
    assert response.get("authenticated") is not True
    assert "hold_token" not in response
    assert "install_allowed" not in response
    assert "nonce" not in response


@pytest.mark.parametrize("route", sorted(PHONE_ROUTES))
def test_phone_routes_remain_disabled_without_explicit_flag(
    route, tmp_path, monkeypatch,
):
    monkeypatch.delenv("ARENA_PHONE_WORKER_API_ENABLED", raising=False)
    handler = Reply({
        "worker_id": "android_test_12345678",
        "token": "invalid-machine-token",
        "nonce": "a" * 32,
    })
    assert dispatch_worker_post(handler, route, tmp_path / "worker.sqlite", {})
    assert handler.responses == [
        (503, {"error": "Android Worker API nie jest jeszcze włączone"})
    ]


def test_unknown_worker_path_cannot_fall_through_to_panel_auth(tmp_path):
    handler = Reply({})
    assert dispatch_worker_post(
        handler, "/api/remote-workers/admin-assignment", tmp_path / "worker.sqlite", {},
    )
    assert handler.responses == [
        (404, {"error": "Nieznana operacja Workera"})
    ]


def test_unrelated_panel_route_is_not_claimed_by_worker_router(tmp_path):
    handler = Reply({})
    assert not dispatch_worker_post(handler, "/api/tournaments/start", tmp_path / "worker.sqlite", {})
    assert handler.responses == []
