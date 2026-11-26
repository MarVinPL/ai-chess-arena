"""Regression tests for panel model inventory, admission and Elo GET (#314)."""
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock

import pytest
import web_panel_inventory_api as api


class Reply:
    def __init__(self):
        self.sent = []

    def send_json(self, payload, status=200):
        self.sent.append((status, payload))


class Connection:
    def __init__(self, rows):
        self.rows = rows
        self.queries = []

    def execute(self, sql):
        self.queries.append(sql)
        return Mock(fetchall=lambda: self.rows)


def connect_to(connection):
    @contextmanager
    def factory():
        yield connection
    return factory


def test_only_known_panel_inventory_routes_are_handled():
    handler = Reply()
    assert len(api.MODEL_INVENTORY_GET_ROUTES) == 4
    assert not api.dispatch_panel_inventory_get(handler, "/api/remote-workers/models")
    assert not api.dispatch_panel_inventory_get(handler, "/api/league/anything")
    assert handler.sent == []


def test_inventory_preserves_license_inference_and_budget_fields(monkeypatch):
    connection = Connection([{
        "id": 7, "name": "Model", "sha256": "abc",
        "draft_sha256": "def", "metadata_json": '{"license_evidence":{"license":"MIT"}}'
    }])
    monkeypatch.setattr(api.league, "connect", connect_to(connection))
    normalize = Mock(return_value={"thinking": True})
    budget = Mock(return_value={"limit": 100})
    monkeypatch.setattr(api.league, "normalize_inference_config", normalize)
    monkeypatch.setattr(api.league, "token_budget_info", budget)
    reply = Reply()
    assert api.dispatch_panel_inventory_get(reply, "/api/models")
    assert len(connection.queries) == 1
    assert reply.sent == [(200, [{
        "id": 7, "name": "Model", "sha256": "abc", "draft_sha256": "def",
        "license_evidence": {"license": "MIT"}, "inference": {"thinking": True},
        "token_budget": {"limit": 100},
    }])]
    budget.assert_called_once_with("abc", "def", {"license_evidence": {"license": "MIT"}})


def test_invalid_metadata_falls_back_without_failing_entire_inventory(monkeypatch):
    connection = Connection([{
        "id": 8, "metadata_json": "{not-json",
    }])
    monkeypatch.setattr(api.league, "connect", connect_to(connection))
    reply = Reply()
    assert api.dispatch_panel_inventory_get(reply, "/api/models")
    assert reply.sent == [(200, [{
        "id": 8, "license_evidence": None, "inference": {"thinking": False},
    }])]


def test_admission_uses_authoritative_connection(monkeypatch):
    connection = Connection([])
    monkeypatch.setattr(api.league, "connect", connect_to(connection))
    listing = Mock(return_value=[{"model_id": 2}])
    monkeypatch.setattr(api.model_admission, "list_decisions", listing)
    reply = Reply()
    assert api.dispatch_panel_inventory_get(reply, "/api/model-admission")
    listing.assert_called_once_with(connection)
    assert reply.sent == [(200, [{"model_id": 2}])]


def test_admission_errors_retain_503_status(monkeypatch):
    connection = Connection([])
    monkeypatch.setattr(api.league, "connect", connect_to(connection))
    def fail(_):
        raise ValueError("unavailable")
    monkeypatch.setattr(api.model_admission, "list_decisions", fail)
    reply = Reply()
    assert api.dispatch_panel_inventory_get(reply, "/api/model-admission")
    assert reply.sent == [(503, {"error": "unavailable"})]


@pytest.mark.parametrize("route,format_name", [
    ("/api/league", "PURE_AI"),
    ("/api/league/engine", "AI_ENGINE"),
])
def test_elo_routes_preserve_original_format(monkeypatch, route, format_name):
    ratings = Mock(return_value=[{"elo": 1250}])
    monkeypatch.setattr(api.league, "elo_ratings", ratings)
    reply = Reply()
    assert api.dispatch_panel_inventory_get(reply, route)
    ratings.assert_called_once_with(format_name)
    assert reply.sent == [(200, [{"elo": 1250}])]


def test_inventory_dispatch_is_behind_browser_authentication():
    root = Path(__file__).resolve().parents[1]
    server = (root / "server.py").read_text(encoding="utf-8")
    get = server.split("    def do_GET(self):", 1)[1].split("    def do_POST(self):", 1)[0]
    assert get.index("if not user:") < get.index(
        "web_panel_inventory_api.dispatch_panel_inventory_get("
    )
    module = (root / "web_panel_inventory_api.py").read_text(encoding="utf-8")
    assert "WorkerTokenStore" not in module
    assert "sqlite3.connect" not in module
