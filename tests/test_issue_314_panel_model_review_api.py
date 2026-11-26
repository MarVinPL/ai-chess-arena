"""Regression tests for panel-only model review/admission extraction (#314)."""
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock

import pytest
import web_panel_model_review_api as api


@contextmanager
def fake_db():
    yield "authoritative-connection"


def test_unknown_or_worker_route_never_dispatches():
    assert len(api.MODEL_REVIEW_POST_ROUTES) == 4
    assert "/api/remote-workers/job-poll" not in api.MODEL_REVIEW_POST_ROUTES
    with pytest.raises(ValueError, match="Unknown"):
        api.handle_model_review_admin_post("/api/remote-workers/job-poll", {})


def test_review_submission_uses_existing_allowlist_and_shared_connection(monkeypatch):
    monkeypatch.setattr(api.league, "connect", fake_db)
    load = Mock(return_value={"models": [
        {"model_id": 1, "status": "eligible"},
        {"model_id": 2, "status": "restricted"},
        {"model_id": 6, "status": "eligible"},
        {"model_id": 3, "status": "blocked"},
    ]})
    submit = Mock(return_value={"saved": True})
    monkeypatch.setattr(api.model_allowlist, "load_allowlist", load)
    monkeypatch.setattr(api.model_review_submissions, "submit", submit)
    payload = {"repo_id": "a/b"}
    assert api.handle_model_review_admin_post(
        "/api/model-review-submissions", payload
    ) == {"saved": True}
    load.assert_called_once_with("authoritative-connection")
    submit.assert_called_once_with(
        "authoritative-connection", payload, allowed_model_ids={1, 2}
    )


def test_review_action_retains_actor_and_allowed_ids(monkeypatch):
    monkeypatch.setattr(api.league, "connect", fake_db)
    monkeypatch.setattr(api.model_allowlist, "load_allowlist",
                        lambda connection: {"models": [{"model_id": 4, "status": "eligible"}]})
    apply = Mock(return_value={"action": "done"})
    monkeypatch.setattr(api.model_review_submissions, "apply_action", apply)
    assert api.handle_model_review_admin_post(
        "/api/model-review-submissions/action",
        {"submission_id": 9, "action": "accept", "proposal": {"model_id": 4}}
    ) == {"action": "done"}
    assert apply.call_args.args == ("authoritative-connection",)
    assert apply.call_args.kwargs == {
        "submission_id": 9, "action": "accept", "actor": "arena",
        "reason": "", "payload": {"model_id": 4}, "allowed_model_ids": {4}
    }


@pytest.mark.parametrize("route,action", [
    ("/api/model-admission/admit", "admit"),
    ("/api/model-admission/revoke", "revoke"),
])
def test_admission_uses_shared_decision_service_with_exact_confirmation(monkeypatch, route, action):
    monkeypatch.setattr(api.league, "connect", fake_db)
    decide = Mock(return_value={"decision": action})
    monkeypatch.setattr(api.model_admission, "apply_decision", decide)
    assert api.handle_model_review_admin_post(
        route, {"model_id": "8", "confirm": True, "reason": "manual"}
    ) == {"decision": action}
    decide.assert_called_once_with("authoritative-connection",
        model_id=8, action=action, confirm=True, reason="manual")
    decide.reset_mock()
    api.handle_model_review_admin_post(route, {"model_id": 8, "confirm": 1})
    assert decide.call_args.kwargs["confirm"] is False


def test_panel_model_review_keeps_browser_auth_origin_before_dispatch():
    src = (Path(__file__).resolve().parents[1] / "server.py").read_text()
    post = src.split("    def do_POST(self):", 1)[1]
    at = post.index("web_panel_model_review_api.handle_model_review_admin_post(")
    assert post.index("worker_backend.dispatch_worker_post(") < at
    assert post.index("if not self.authenticated_user():") < at
    assert post.index("if not arena_auth.origin_allowed(") < at
    module = (Path(__file__).resolve().parents[1] / "web_panel_model_review_api.py").read_text()
    assert "WorkerTokenStore" not in module
    assert "sqlite3.connect" not in module
