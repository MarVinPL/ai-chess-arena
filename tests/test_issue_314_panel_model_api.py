"""Panel model API must remain authenticated and share Arena domain state (#314)."""
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
import web_panel_model_api as api


class Reply:
    def __init__(self):
        self.responses = []

    def send_json(self, value, status=200):
        self.responses.append((status, value))


@contextmanager
def database():
    # A single injected connection acts as the shared domain reference.
    yield "shared_arena_connection"


def test_panel_model_reads_delegate_to_shared_backend(monkeypatch):
    monkeypatch.setattr(api.league, "connect", database)
    monkeypatch.setattr(api.model_permissions, "list_permissions",
                        lambda conn: {"source": conn})
    monkeypatch.setattr(api.model_review_submissions, "list_submissions",
                        lambda conn: [{"source": conn}])
    reply = Reply()
    assert api.dispatch_panel_model_get(reply, "/api/model-permissions")
    assert reply.responses == [(200, {"source": "shared_arena_connection"})]
    reply = Reply()
    assert api.dispatch_panel_model_get(reply, "/api/model-review-submissions")
    assert reply.responses == [(200, [{"source": "shared_arena_connection"}])]
    reply = Reply()
    assert not api.dispatch_panel_model_get(reply, "/api/remote-workers/job-poll")
    assert not reply.responses


def test_panel_model_read_preserves_error_contract(monkeypatch):
    monkeypatch.setattr(api.league, "connect", database)
    def fail(_connection):
        raise RuntimeError("backend unavailable")
    monkeypatch.setattr(api.model_permissions, "list_permissions", fail)
    reply = Reply()
    assert api.dispatch_panel_model_get(reply, "/api/model-permissions")
    assert reply.responses == [(500, {"error": "backend unavailable"})]


def test_permission_request_reuses_shared_connection_and_actor(monkeypatch):
    monkeypatch.setattr(api.league, "connect", database)
    seen = {}
    def request(connection, **kwargs):
        seen.update(connection=connection, **kwargs)
        return {"ok": True}
    monkeypatch.setattr(api.model_permissions, "request_permission", request)
    assert api.handle_permission_post("/api/model-permissions/request",
                                     {"model_id": 7, "confirm": True},
                                     "admin") == {"ok": True}
    assert seen["connection"] == "shared_arena_connection"
    assert seen["actor"] == "admin"
    assert seen["model_id"] == 7 and seen["confirm"] is True


def test_permission_decision_rejects_bool_id_before_db(monkeypatch):
    def unexpected_connect():
        pytest.fail("Invalid ID must never open DB")
    monkeypatch.setattr(api.league, "connect", unexpected_connect)
    with pytest.raises(ValueError, match="identyfikator"):
        api.handle_permission_post("/api/model-permissions/decision",
                                   {"request_id": True}, "admin")
    with pytest.raises(ValueError, match="identyfikator"):
        api.handle_permission_post("/api/model-permissions/decision",
                                   {"request_id": "3"}, "admin")


def test_permission_decision_keeps_original_actor_and_confirmation(monkeypatch):
    monkeypatch.setattr(api.league, "connect", database)
    seen = {}
    def decide(connection, **kwargs):
        seen.update(connection=connection, **kwargs)
        return {"decision": "accepted"}
    monkeypatch.setattr(api.model_permissions, "decide_permission", decide)
    result = api.handle_permission_post("/api/model-permissions/decision",
             {"request_id": 3, "action": "approve", "confirm": False}, "admin")
    assert result == {"decision": "accepted"}
    assert seen["connection"] == "shared_arena_connection"
    assert seen["request_id"] == 3 and seen["confirm"] is False
    assert seen["actor"] == "admin"


def test_panel_model_api_remains_behind_session_and_origin_gates():
    root = Path(__file__).resolve().parents[1]
    source = (root / "server.py").read_text(encoding="utf-8")
    get = source.split("    def do_GET(self):", 1)[1].split("    def do_POST(self):", 1)[0]
    assert get.index("if not user:") < get.index("dispatch_panel_model_get(self, path)")
    post = source.split("    def do_POST(self):", 1)[1]
    assert post.index("if not self.authenticated_user():") < post.index(
        "handle_permission_post("
    )
    assert post.index("if not arena_auth.origin_allowed(") < post.index(
        "handle_permission_post("
    )
    assert post.index("worker_backend.dispatch_worker_post(") < post.index(
        "handle_permission_post("
    )
    module = (root / "web_panel_model_api.py").read_text(encoding="utf-8")
    assert "WorkerTokenStore" not in module
    assert "sqlite3.connect" not in module
