"""Issue #314: browser-only tournament GET delegation and domain contract."""
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock
import pytest

import web_panel_tournament_read_api as api


class Handler:
    def __init__(self):
        self.sent = []

    def send_json(self, data, status=200):
        self.sent.append((status, data))


class DB:
    def __init__(self, results):
        self.results = results
        self.calls = []

    def execute(self, sql, args=()):
        self.calls.append((sql, args))
        return Mock(fetchall=lambda: self.results, fetchone=lambda: (
            self.results[0] if self.results else None))


def connect_to(db):
    @contextmanager
    def context():
        yield db
    return context


def dispatch(handler, path, *, live=None, preflight=None):
    return api.dispatch_panel_tournament_get(
        handler, path, get_live=live or (lambda: {"stage": "running"}),
        tournament_preflight=preflight or (lambda tournament_id: {"id": tournament_id})
    )


def test_unknown_routes_and_worker_api_are_not_intercepted():
    h = Handler()
    assert not dispatch(h, "/api/remote-workers/tournaments")
    assert not dispatch(h, "/api/tournaments-extra")
    assert not dispatch(h, "/api/tournaments/3/moves")
    assert h.sent == []


def test_list_reads_existing_league_db_and_returns_dict_rows(monkeypatch):
    db = DB([{"id": 3, "players": 5}, {"id": 2, "players": 4}])
    monkeypatch.setattr(api.league, "connect", connect_to(db))
    h = Handler()
    assert dispatch(h, "/api/tournaments")
    assert h.sent == [(200, db.results)]
    sql = db.calls[0][0]
    assert "total_games_planned" in sql
    assert "has_checkpoint" in sql
    assert "late_admission_used" in sql


def test_tournament_view_uses_shared_live_snapshot(monkeypatch):
    view = Mock(return_value={"stage": "draft"})
    monkeypatch.setattr(api.league, "tournament_view", view)
    h = Handler()
    assert dispatch(h, "/api/tournaments/7/view")
    view.assert_called_once_with(7, {"stage": "running"})
    assert h.sent == [(200, {"stage": "draft"})]


def test_tournament_view_preserves_404_and_bad_id_422(monkeypatch):
    view = Mock(side_effect=KeyError("unknown"))
    monkeypatch.setattr(api.league, "tournament_view", view)
    h = Handler()
    assert dispatch(h, "/api/tournaments/7/view")
    assert h.sent == [(404, {"error": "'unknown'"})]
    h = Handler()
    assert dispatch(h, "/api/tournaments/not-an-id/view")
    assert h.sent == [(422, {"error": "Nieprawidłowy identyfikator turnieju"})]


def test_preflight_checks_tournament_existence_before_domain_callback(monkeypatch):
    db = DB([])
    monkeypatch.setattr(api.league, "connect", connect_to(db))
    callback = Mock()
    h = Handler()
    assert api.dispatch_panel_tournament_get(
        h, "/api/tournaments/8/preflight",
        get_live=lambda: {}, tournament_preflight=callback)
    assert h.sent == [(404, {"error": "Nie znaleziono turnieju"})]
    callback.assert_not_called()
    assert db.calls[0][1] == (8,)


def test_preflight_uses_shared_callback_only_for_existing_tournament(monkeypatch):
    db = DB([{"exists": 1}])
    monkeypatch.setattr(api.league, "connect", connect_to(db))
    preflight = Mock(return_value={"ready": True})
    h = Handler()
    assert api.dispatch_panel_tournament_get(
        h, "/api/tournaments/8/preflight",
        get_live=lambda: {}, tournament_preflight=preflight)
    preflight.assert_called_once_with(8)
    assert h.sent == [(200, {"ready": True})]


@pytest.mark.parametrize("route", [
    "/api/tournaments/not-an-id/preflight",
    "/api/tournaments/8x/preflight",
])
def test_bad_preflight_id_returns_422(route):
    h = Handler()
    assert dispatch(h, route)
    assert h.sent == [(422, {"error": "Nieprawidłowy identyfikator turnieju"})]


def test_tournament_panel_session_gate_precedes_dispatch():
    root = Path(__file__).resolve().parents[1]
    src = (root / "server.py").read_text(encoding="utf-8")
    get = src.split("    def do_GET(self):", 1)[1].split("    def do_POST(self):", 1)[0]
    assert get.index("if not user:") < get.index(
        "web_panel_tournament_read_api.dispatch_panel_tournament_get(")
    module = (root / "web_panel_tournament_read_api.py").read_text(encoding="utf-8")
    assert "WorkerTokenStore" not in module
    assert "sqlite3.connect" not in module
