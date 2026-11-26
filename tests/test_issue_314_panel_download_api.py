"""Panel download operations cannot bypass browser authorization (#314)."""
from pathlib import Path

import pytest
import web_panel_download_api as api


def test_admin_download_routes_are_exact():
    assert len(api.ADMIN_DOWNLOAD_POST_ROUTES) == 4
    assert "/api/remote-workers/job-poll" not in api.ADMIN_DOWNLOAD_POST_ROUTES
    assert "/api/hf-candidates/download-start/../secret" not in api.ADMIN_DOWNLOAD_POST_ROUTES
    with pytest.raises(ValueError, match="Unknown"):
        api.handle_download_admin_post("/api/remote-workers/job-poll", {})


@pytest.mark.parametrize("route,confirmation", [
    ("/api/hf-candidates/download-start", "confirm_download"),
    ("/api/hf-candidates/download-resume", "confirm_resume"),
])
def test_explicit_confirmation_is_required_before_download_action(monkeypatch, route, confirmation):
    def unexpected(*args, **kwargs):
        pytest.fail("Download service must not be invoked without confirmation")
    monkeypatch.setattr(api.hf_candidate_downloads, "start", unexpected)
    monkeypatch.setattr(api.hf_candidate_downloads, "resume", unexpected)
    with pytest.raises(ValueError, match="zatwierdzenie"):
        api.handle_download_admin_post(route, {"job_id": "1"})
    with pytest.raises(ValueError, match="zatwierdzenie"):
        api.handle_download_admin_post(route, {confirmation: 1, "job_id": "1"})


def test_start_uses_shared_download_service_and_manual_source(monkeypatch):
    captured = {}
    def start(candidates, source):
        captured.update(candidates=candidates, source=source)
        return {"state": "queued"}
    monkeypatch.setattr(api.hf_candidate_downloads, "start", start)
    result = api.handle_download_admin_post("/api/hf-candidates/download-start", {
        "confirm_download": True, "candidates": ["repo/model"]
    })
    assert result == {"state": "queued"}
    assert captured == {"candidates": ["repo/model"], "source": "manual"}


def test_cancel_resume_and_plan_refresh_use_existing_service(monkeypatch):
    calls = []
    monkeypatch.setattr(api.hf_candidate_downloads, "cancel",
                        lambda job_id: calls.append(("cancel", job_id)) or "cancelled")
    monkeypatch.setattr(api.hf_candidate_downloads, "resume",
                        lambda job_id: calls.append(("resume", job_id)) or "resumed")
    monkeypatch.setattr(api.hf_candidate_downloads, "refresh_plan",
                        lambda: calls.append(("refresh", None)) or "refreshed")
    assert api.handle_download_admin_post("/api/hf-candidates/download-cancel",
                                          {"job_id": "j1"}) == "cancelled"
    assert api.handle_download_admin_post("/api/hf-candidates/download-resume",
                                          {"job_id": "j2", "confirm_resume": True}) == "resumed"
    assert api.handle_download_admin_post("/api/hf-candidates/download-plan/refresh",
                                          {}) == "refreshed"
    assert calls == [("cancel", "j1"), ("resume", "j2"), ("refresh", None)]


def test_panel_dispatch_is_behind_session_origin_and_worker_boundary():
    source = (Path(__file__).resolve().parents[1] / "server.py").read_text()
    post = source.split("    def do_POST(self):", 1)[1]
    assert post.index("worker_backend.dispatch_worker_post(") < post.index(
        "web_panel_download_api.handle_download_admin_post("
    )
    assert post.index("if not self.authenticated_user():") < post.index(
        "web_panel_download_api.handle_download_admin_post("
    )
    assert post.index("if not arena_auth.origin_allowed(") < post.index(
        "web_panel_download_api.handle_download_admin_post("
    )
