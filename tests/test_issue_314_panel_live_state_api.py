"""Issue #314: panel live/state snapshots retain the shared Arena game state."""
from pathlib import Path
from unittest.mock import Mock

import web_panel_live_state_api as api


class Reply:
    def __init__(self):
        self.sent = []

    def send_json(self, payload, status=200):
        self.sent.append((status, payload))


def test_only_explicit_panel_snapshot_routes_are_handled():
    reply = Reply()
    state = Mock()
    live = Mock()
    assert api.STATE_GET_ROUTES == {"/api/state", "/api/live"}
    assert not api.dispatch_panel_live_state_get(
        reply, "/api/remote-workers/state",
        state_snapshot=state, live_snapshot=live)
    assert not api.dispatch_panel_live_state_get(
        reply, "/api/state/extra",
        state_snapshot=state, live_snapshot=live)
    state.assert_not_called()
    live.assert_not_called()
    assert reply.sent == []


def test_state_snapshot_is_supplied_by_server_not_copied():
    reply = Reply()
    result = {"fen": "game-fen", "moves": ["e2e4"]}
    state = Mock(return_value=result)
    live = Mock()
    assert api.dispatch_panel_live_state_get(
        reply, "/api/state", state_snapshot=state, live_snapshot=live)
    assert reply.sent == [(200, result)]
    assert reply.sent[0][1] is result
    state.assert_called_once_with()
    live.assert_not_called()


def test_live_snapshot_uses_authoritative_callback():
    reply = Reply()
    result = {"running": True, "move": "e2e4"}
    state = Mock()
    live = Mock(return_value=result)
    assert api.dispatch_panel_live_state_get(
        reply, "/api/live", state_snapshot=state, live_snapshot=live)
    assert reply.sent == [(200, result)]
    assert reply.sent[0][1] is result
    state.assert_not_called()
    live.assert_called_once_with()


def test_panel_session_gate_precedes_state_get_dispatch():
    root = Path(__file__).resolve().parents[1]
    server = (root / "server.py").read_text(encoding="utf-8")
    get = server.split("    def do_GET(self):", 1)[1].split("    def do_POST(self):", 1)[0]
    assert get.index("if not user:") < get.index(
        "web_panel_live_state_api.dispatch_panel_live_state_get(")


def test_state_read_still_holds_shared_lock_while_snapshotting():
    server = (Path(__file__).resolve().parents[1] / "server.py").read_text(encoding="utf-8")
    a = server.split("def _panel_state_snapshot():", 1)[1].split(
        "class Handler(BaseHTTPRequestHandler):", 1)[0]
    assert "with lock:" in a
    assert "return state()" in a
    module = (Path(__file__).resolve().parents[1] / "web_panel_live_state_api.py").read_text(
        encoding="utf-8")
    assert "chess.Board()" not in module
    assert "WorkerTokenStore" not in module
    assert "sqlite3.connect" not in module
