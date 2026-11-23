"""Issue #314: HF read status stays panel-only and contract-compatible."""
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock
import pytest
import web_panel_hf_status_api as api


class Handler:
    def __init__(self):
        self.sent = []
    def send_json(self, value, status=200):
        self.sent.append((status, value))


@contextmanager
def database():
    yield "shared-connection"


def test_only_five_exact_routes_are_accepted():
    assert len(api.HF_STATUS_GET_ROUTES) == 5
    handler = Handler()
    assert not api.dispatch_panel_hf_status_get(
        handler, "/api/remote-workers/job-poll", "/api/remote-workers/job-poll")
    assert not handler.sent


def test_review_queries_existing_shared_database(monkeypatch):
    monkeypatch.setattr(api.league, "connect", database)
    get = Mock(return_value={"review": "accepted"})
    monkeypatch.setattr(api.hf_candidate_reviews, "get", get)
    handler = Handler()
    assert api.dispatch_panel_hf_status_get(
        handler, "/api/hf-candidates/review",
        "/api/hf-candidates/review?catalog_id=a%2Fb&revision=main")
    get.assert_called_once_with("shared-connection", "a/b", "main")
    assert handler.sent == [(200, {"review": "accepted"})]


def test_review_missing_retains_not_started_contract(monkeypatch):
    monkeypatch.setattr(api.league, "connect", database)
    monkeypatch.setattr(api.hf_candidate_reviews, "get", lambda *_: None)
    handler = Handler()
    assert api.dispatch_panel_hf_status_get(
        handler, "/api/hf-candidates/review", "/api/hf-candidates/review")
    assert handler.sent == [(200, {"review": None, "workflow_status": "not_started"})]


def test_review_queue_delegates_to_shared_service(monkeypatch):
    monkeypatch.setattr(api.league, "connect", database)
    fetch = Mock(return_value=[{"id": 2}])
    monkeypatch.setattr(api.hf_candidate_reviews, "list_proposals", fetch)
    handler = Handler()
    assert api.dispatch_panel_hf_status_get(
        handler, "/api/hf-candidates/review-queue", "/api/hf-candidates/review-queue")
    fetch.assert_called_once_with("shared-connection")
    assert handler.sent == [(200, [{"id": 2}])]


@pytest.mark.parametrize("route,attribute,service", [
    ("/api/hf-candidates/audit-queue", "snapshot", "queue"),
    ("/api/hf-candidates/hardware", "hardware_snapshot", "queue"),
    ("/api/hf-candidates/runtime-preflight", "snapshot", "runtime"),
])
def test_preflight_status_uses_existing_services(monkeypatch, route, attribute, service):
    module = api.hf_candidate_queue if service == "queue" else api.hf_candidate_runtime
    fn = Mock(return_value={"stage": route})
    monkeypatch.setattr(module, attribute, fn)
    handler = Handler()
    assert api.dispatch_panel_hf_status_get(handler, route, route)
    fn.assert_called_once_with()
    assert handler.sent == [(200, {"stage": route})]


@pytest.mark.parametrize("route,status,attribute,module_name", [
    ("/api/hf-candidates/review-queue", 422, "list_proposals", "reviews"),
    ("/api/hf-candidates/audit-queue", 503, "snapshot", "queue"),
    ("/api/hf-candidates/hardware", 503, "hardware_snapshot", "queue"),
    ("/api/hf-candidates/runtime-preflight", 503, "snapshot", "runtime"),
])
def test_existing_failure_status_codes_preserved(monkeypatch, route, status, attribute, module_name):
    monkeypatch.setattr(api.league, "connect", database)
    module = {
        "reviews": api.hf_candidate_reviews,
        "queue": api.hf_candidate_queue,
        "runtime": api.hf_candidate_runtime,
    }[module_name]
    def fail(*_):
        raise OSError("unavailable")
    monkeypatch.setattr(module, attribute, fail)
    handler = Handler()
    assert api.dispatch_panel_hf_status_get(handler, route, route)
    assert handler.sent == [(status, {"error": "unavailable"})]


def test_session_gate_and_worker_separation():
    root = Path(__file__).resolve().parents[1]
    src = (root / "server.py").read_text(encoding="utf-8")
    get = src.split("    def do_GET(self):", 1)[1].split("    def do_POST(self):", 1)[0]
    assert get.index("if not user:") < get.index(
        "web_panel_hf_status_api.dispatch_panel_hf_status_get(")
    module = (root / "web_panel_hf_status_api.py").read_text(encoding="utf-8")
    assert "WorkerTokenStore" not in module
    assert "sqlite3.connect" not in module
