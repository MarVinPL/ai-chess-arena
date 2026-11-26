"""Security and behavior contracts for panel HF candidate APIs (#314)."""
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock

import pytest
import web_panel_hf_review_api as api


@contextmanager
def fake_db():
    yield "shared-connection"


def test_exact_routes_refuse_worker_and_unrecognized_urls():
    assert len(api.HF_REVIEW_POST_ROUTES) == 3
    assert "/api/remote-workers/job-poll" not in api.HF_REVIEW_POST_ROUTES
    with pytest.raises(ValueError, match="Unknown"):
        api.handle_hf_review_admin_post("/api/remote-workers/job-poll", {}, actor="admin")


def test_save_review_preserves_authenticated_actor_and_shared_connection(monkeypatch):
    monkeypatch.setattr(api.league, "connect", fake_db)
    save = Mock(return_value={"state": "saved"})
    monkeypatch.setattr(api.hf_candidate_reviews, "save", save)
    assert api.handle_hf_review_admin_post(
        "/api/hf-candidates/review", {"catalog_id": "x"}, actor="admin"
    ) == {"state": "saved"}
    save.assert_called_once_with("shared-connection", {"catalog_id": "x"}, actor="admin")


def test_review_action_uses_same_catalog_and_actor(monkeypatch):
    monkeypatch.setattr(api.league, "connect", fake_db)
    monkeypatch.setattr(api.hf_candidate_audit, "load_catalog",
                        lambda: {"catalog": "original"})
    apply = Mock(return_value={"state": "applied"})
    monkeypatch.setattr(api.hf_candidate_reviews, "apply_proposal_action", apply)
    assert api.handle_hf_review_admin_post(
        "/api/hf-candidates/review-action",
        {"proposal_id": 4, "action": "approve"}, actor="admin"
    ) == {"state": "applied"}
    assert apply.call_args.kwargs["actor"] == "admin"
    assert apply.call_args.kwargs["catalog"] == {"catalog": "original"}
    assert apply.call_args.args == ("shared-connection",)


def test_audit_lock_busy_does_not_call_audit_or_db(monkeypatch):
    lock = Mock()
    lock.acquire.return_value = False
    monkeypatch.setattr(api.hf_candidate_audit, "AUDIT_LOCK", lock)
    audit = Mock()
    monkeypatch.setattr(api.hf_candidate_audit, "audit_candidate", audit)
    with pytest.raises(ValueError, match="Inny audyt"):
        api.handle_hf_review_admin_post("/api/hf-candidates/audit",
                                        {"catalog_id": 5}, actor="admin")
    audit.assert_not_called()
    lock.release.assert_not_called()


def test_audit_report_uses_shared_db_and_releases_lock(monkeypatch):
    monkeypatch.setattr(api.league, "connect", fake_db)
    lock = Mock()
    lock.acquire.return_value = True
    monkeypatch.setattr(api.hf_candidate_audit, "AUDIT_LOCK", lock)
    monkeypatch.setattr(api.hf_candidate_audit, "audit_candidate",
                        lambda catalog_id: {"catalog_id": catalog_id})
    saved = Mock()
    monkeypatch.setattr(api.hf_candidate_audit, "save_report", saved)
    assert api.handle_hf_review_admin_post(
        "/api/hf-candidates/audit", {"catalog_id": "abc"}, actor="admin"
    ) == {"catalog_id": "abc"}
    saved.assert_called_once_with("shared-connection", {"catalog_id": "abc"})
    lock.release.assert_called_once()


def test_audit_lock_released_after_exception(monkeypatch):
    lock = Mock()
    lock.acquire.return_value = True
    monkeypatch.setattr(api.hf_candidate_audit, "AUDIT_LOCK", lock)
    def fail(_id):
        raise RuntimeError("audit-failed")
    monkeypatch.setattr(api.hf_candidate_audit, "audit_candidate", fail)
    with pytest.raises(RuntimeError, match="audit-failed"):
        api.handle_hf_review_admin_post("/api/hf-candidates/audit",
                                        {"catalog_id": "abc"}, actor="admin")
    lock.release.assert_called_once()


def test_hf_admin_still_requires_browser_session_and_origin():
    source = (Path(__file__).resolve().parents[1] / "server.py").read_text()
    post = source.split("    def do_POST(self):", 1)[1]
    dispatch = post.index("web_panel_hf_review_api.handle_hf_review_admin_post(")
    assert post.index("worker_backend.dispatch_worker_post(") < dispatch
    assert post.index("if not self.authenticated_user():") < dispatch
    assert post.index("if not arena_auth.origin_allowed(") < dispatch
    module = (Path(__file__).resolve().parents[1] / "web_panel_hf_review_api.py").read_text()
    assert "WorkerTokenStore" not in module
    assert "sqlite3.connect" not in module
