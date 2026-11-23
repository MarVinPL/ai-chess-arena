"""Contract and authorization regression tests for panel HF download GETs (#314)."""
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

import web_panel_download_status_api as api


class Response:
    def __init__(self):
        self.sent = []

    def send_json(self, payload, status=200):
        self.sent.append((status, payload))


def test_exact_download_get_routes_do_not_match_worker_paths():
    assert len(api.DOWNLOAD_GET_ROUTES) == 5
    reply = Response()
    assert api.dispatch_panel_download_get(
        reply, "/api/remote-workers/job-poll", "/api/remote-workers/job-poll"
    ) is False
    assert not reply.sent


def test_download_plan_and_history_use_authoritative_service(monkeypatch):
    monkeypatch.setattr(api.hf_candidate_downloads, "plan_snapshot",
                        lambda: {"state": "ready"})
    monkeypatch.setattr(api.hf_candidate_downloads, "history",
                        lambda: [{"job_id": "j"}])
    reply = Response()
    assert api.dispatch_panel_download_get(reply,
        "/api/hf-candidates/download-plan", "/api/hf-candidates/download-plan")
    assert reply.sent == [(200, {"state": "ready"})]
    reply = Response()
    assert api.dispatch_panel_download_get(reply,
        "/api/hf-candidates/download-history", "/api/hf-candidates/download-history")
    assert reply.sent == [(200, {"jobs": [{"job_id": "j"}]})]


def test_download_events_require_job_id_before_service(monkeypatch):
    events = Mock()
    monkeypatch.setattr(api.hf_candidate_downloads, "events", events)
    reply = Response()
    assert api.dispatch_panel_download_get(reply,
        "/api/hf-candidates/download-events", "/api/hf-candidates/download-events")
    assert reply.sent == [(422, {"error": "Brak job_id"})]
    events.assert_not_called()


def test_download_events_decode_query_and_preserve_response(monkeypatch):
    events = Mock(return_value=[{"event": "downloaded"}])
    monkeypatch.setattr(api.hf_candidate_downloads, "events", events)
    reply = Response()
    assert api.dispatch_panel_download_get(reply,
        "/api/hf-candidates/download-events",
        "/api/hf-candidates/download-events?job_id=a%20b")
    events.assert_called_once_with("a b")
    assert reply.sent == [(200, {"events": [{"event": "downloaded"}]})]


def test_download_files_uses_catalog_id(monkeypatch):
    list_files = Mock(return_value=[{"filename": "model.gguf"}])
    monkeypatch.setattr(api.hf_candidate_downloads, "list_files", list_files)
    reply = Response()
    assert api.dispatch_panel_download_get(reply,
        "/api/hf-candidates/download-files",
        "/api/hf-candidates/download-files?catalog_id=a%2Fb")
    list_files.assert_called_once_with("a/b")
    assert reply.sent == [(200, [{"filename": "model.gguf"}])]


def test_jobs_fetch_or_current_and_404(monkeypatch):
    get = Mock(return_value={"job_id": "x"})
    current = Mock(return_value=None)
    monkeypatch.setattr(api.hf_candidate_downloads, "get", get)
    monkeypatch.setattr(api.hf_candidate_downloads, "current", current)
    reply = Response()
    assert api.dispatch_panel_download_get(reply,
        "/api/hf-candidates/download-jobs",
        "/api/hf-candidates/download-jobs?job_id=x")
    get.assert_called_once_with("x")
    assert reply.sent == [(200, {"job_id": "x"})]
    reply = Response()
    assert api.dispatch_panel_download_get(reply,
        "/api/hf-candidates/download-jobs", "/api/hf-candidates/download-jobs")
    current.assert_called_once_with()
    assert reply.sent == [(404, {"error": "Nie znaleziono zadania."})]


def test_download_exception_status_is_preserved(monkeypatch):
    def fail():
        raise OSError("storage unavailable")
    monkeypatch.setattr(api.hf_candidate_downloads, "history", fail)
    reply = Response()
    assert api.dispatch_panel_download_get(reply,
        "/api/hf-candidates/download-history", "/api/hf-candidates/download-history")
    assert reply.sent == [(422, {"error": "storage unavailable"})]


def test_panel_authentication_gate_precedes_dispatch():
    root = Path(__file__).resolve().parents[1]
    server = (root / "server.py").read_text(encoding="utf-8")
    get = server.split("    def do_GET(self):", 1)[1].split("    def do_POST(self):", 1)[0]
    assert get.index("if not user:") < get.index(
        "web_panel_download_status_api.dispatch_panel_download_get("
    )
    module = (root / "web_panel_download_status_api.py").read_text(encoding="utf-8")
    assert "WorkerTokenStore" not in module
    assert "arena_auth.session_user" not in module
