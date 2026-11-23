"""Issue #314: panel tournament admin remains session-only and state-shared."""
from pathlib import Path
from unittest.mock import Mock

import pytest

import web_panel_tournament_api as api


def invoke(route, payload=None, thread=None, start=None, stop=None):
    return api.handle_tournament_admin_post(
        route, payload or {},
        start_tournament=start or Mock(return_value={"state": "started"}),
        active_thread=thread, stop_event=stop or Mock())


def test_only_explicit_tournament_admin_routes_are_accepted():
    assert len(api.TOURNAMENT_POST_ROUTES) == 5
    assert not any(route.startswith("/api/remote-workers/") for route in api.TOURNAMENT_POST_ROUTES)
    with pytest.raises(ValueError, match="Unknown"):
        invoke("/api/remote-workers/stop", {})


def test_start_preserves_id_and_existing_start_callback():
    start = Mock(return_value={"status": "started"})
    assert invoke("/api/tournaments/start", {"tournament_id": "17"}, start=start) == {
        "status": "started"
    }
    start.assert_called_once_with(17)


def test_live_tournament_blocks_admissions_and_entry_edits(monkeypatch):
    active = Mock()
    active.is_alive.return_value = True
    add = Mock()
    edit = Mock()
    monkeypatch.setattr(api.league, "create_late_admission_batch", add)
    monkeypatch.setattr(api.league, "add_prestart_tournament_entries", edit)
    with pytest.raises(RuntimeError, match="zatrzymaj"):
        invoke("/api/tournaments/admissions", {"parent_tournament_id": 9}, thread=active)
    with pytest.raises(RuntimeError, match="aktywnego"):
        invoke("/api/tournaments/entries", {"tournament_id": 9}, thread=active)
    add.assert_not_called()
    edit.assert_not_called()


def test_idle_tournament_admissions_use_shared_league(monkeypatch):
    admission = Mock(return_value={"status": "added"})
    entries = Mock(return_value={"status": "entries"})
    monkeypatch.setattr(api.league, "create_late_admission_batch", admission)
    monkeypatch.setattr(api.league, "add_prestart_tournament_entries", entries)
    ids = [1, 3]
    assert invoke("/api/tournaments/admissions", {
        "parent_tournament_id": "7", "model_ids": ids
    }) == {"status": "added"}
    admission.assert_called_once_with(7, ids)
    assert invoke("/api/tournaments/entries", {
        "tournament_id": "8", "model_ids": ids
    }) == {"status": "entries"}
    entries.assert_called_once_with(8, ids)


def test_stop_requests_event_only_when_thread_is_live():
    event = Mock()
    live = Mock()
    live.is_alive.return_value = True
    result = invoke("/api/tournaments/stop", thread=live, stop=event)
    assert result == {"status": "stop_requested", "checkpoint_required": True}
    event.set.assert_called_once()
    inactive_event = Mock()
    assert invoke("/api/tournaments/stop", stop=inactive_event) == {
        "status": "not_running"
    }
    inactive_event.set.assert_not_called()


def test_create_tournament_keeps_original_defaults(monkeypatch):
    create = Mock(return_value={"id": 11})
    monkeypatch.setattr(api.league, "create_round_robin", create)
    assert invoke("/api/tournaments") == {"id": 11}
    create.assert_called_once_with("Tournament", "PURE_AI", 80)


def test_tournament_admin_is_after_browser_session_and_origin_checks():
    root = Path(__file__).resolve().parents[1]
    source = (root / "server.py").read_text(encoding="utf-8")
    post = source.split("    def do_POST(self):", 1)[1]
    dispatch = post.index("web_panel_tournament_api.handle_tournament_admin_post(")
    assert post.index("worker_backend.dispatch_worker_post(") < dispatch
    assert post.index("if not self.authenticated_user():") < dispatch
    assert post.index("if not arena_auth.origin_allowed(") < dispatch
    assert "TOURNAMENT_POST_ROUTES" in post
    adapter = (root / "web_panel_tournament_api.py").read_text(encoding="utf-8")
    assert "WorkerTokenStore" not in adapter
    assert "sqlite3.connect" not in adapter
