"""Contract tests for authenticated HF catalog and audit GET API (#314)."""
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock

import pytest
import web_panel_hf_catalog_api as api


class Handler:
    def __init__(self):
        self.sent = []

    def send_json(self, value, status=200):
        self.sent.append((status, value))


@contextmanager
def database():
    yield "authoritative-db"


def test_unknown_and_worker_routes_are_not_handled():
    h = Handler()
    assert not api.dispatch_panel_hf_catalog_get(
        h, "/api/remote-workers/jobs", "/api/remote-workers/jobs")
    assert not api.dispatch_panel_hf_catalog_get(
        h, "/api/hf-candidates-extra", "/api/hf-candidates-extra")
    assert h.sent == []


def test_single_audit_lookup_decodes_query_and_reuses_league(monkeypatch):
    monkeypatch.setattr(api.league, "connect", database)
    get = Mock(return_value={"catalog_id": "org/x", "result": "passed"})
    monkeypatch.setattr(api.hf_candidate_audit, "get_report", get)
    h = Handler()
    assert api.dispatch_panel_hf_catalog_get(
        h, "/api/hf-candidates/audit",
        "/api/hf-candidates/audit?catalog_id=org%2Fx&revision=main")
    get.assert_called_once_with("authoritative-db", "org/x", "main")
    assert h.sent == [(200, {"catalog_id": "org/x", "result": "passed"})]


def test_missing_saved_audit_preserves_404(monkeypatch):
    monkeypatch.setattr(api.league, "connect", database)
    monkeypatch.setattr(api.hf_candidate_audit, "get_report", lambda *_: None)
    h = Handler()
    assert api.dispatch_panel_hf_catalog_get(
        h, "/api/hf-candidates/audit", "/api/hf-candidates/audit")
    assert h.sent == [(404, {"error": "Brak zapisanego audytu dla tej rewizji."})]


def test_audit_lookup_error_returns_422(monkeypatch):
    monkeypatch.setattr(api.league, "connect", database)
    def fail(*args):
        raise ValueError("invalid revision")
    monkeypatch.setattr(api.hf_candidate_audit, "get_report", fail)
    h = Handler()
    assert api.dispatch_panel_hf_catalog_get(
        h, "/api/hf-candidates/audit", "/api/hf-candidates/audit")
    assert h.sent == [(422, {"error": "invalid revision"})]


def test_catalog_preserves_report_review_mapping_and_projection(monkeypatch):
    monkeypatch.setattr(api.league, "connect", database)
    items = [
        {"catalog_id": "first", "revision": "rev1", "display_name": "One",
         "repo_id": "org/one", "internal_secret": "not an API field"},
        {"catalog_id": "second", "audited_revision": "rev2", "display_name": "Two"},
    ]
    monkeypatch.setattr(api.hf_candidate_audit, "load_catalog", lambda: items)
    report = {"catalog_id": "first", "revision": "rev1", "verdict": "pass"}
    review = {"catalog_id": "second", "revision": "rev2", "state": "accepted"}
    reports = Mock(return_value=[report])
    reviews = Mock(return_value=[review])
    monkeypatch.setattr(api.hf_candidate_audit, "list_reports", reports)
    monkeypatch.setattr(api.hf_candidate_reviews, "list_reviews", reviews)
    h = Handler()
    assert api.dispatch_panel_hf_catalog_get(
        h, "/api/hf-candidates", "/api/hf-candidates")
    status, rows = h.sent[0]
    assert status == 200 and len(rows) == 2
    assert rows[0]["last_audit"] == report and rows[0]["last_review"] is None
    assert rows[1]["last_review"] == review and rows[1]["last_audit"] is None
    assert "internal_secret" not in rows[0]
    assert set(rows[0]) == set(api.CANDIDATE_FIELDS) | {"last_audit", "last_review"}
    reports.assert_called_once_with("authoritative-db")
    reviews.assert_called_once_with("authoritative-db")


def test_catalog_error_returns_503(monkeypatch):
    def fail():
        raise OSError("catalog unavailable")
    monkeypatch.setattr(api.hf_candidate_audit, "load_catalog", fail)
    h = Handler()
    assert api.dispatch_panel_hf_catalog_get(
        h, "/api/hf-candidates", "/api/hf-candidates")
    assert h.sent == [(503, {"error": "catalog unavailable"})]


def test_dispatch_is_after_browser_authentication():
    src = (Path(__file__).resolve().parents[1] / "server.py").read_text("utf-8")
    get = src.split("    def do_GET(self):", 1)[1].split("    def do_POST(self):", 1)[0]
    assert get.index("if not user:") < get.index(
        "web_panel_hf_catalog_api.dispatch_panel_hf_catalog_get(")
    module = (Path(__file__).resolve().parents[1] / "web_panel_hf_catalog_api.py").read_text("utf-8")
    assert "WorkerTokenStore" not in module
    assert "sqlite3.connect" not in module
