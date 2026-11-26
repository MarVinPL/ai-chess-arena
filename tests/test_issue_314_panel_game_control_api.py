"""Issue #314: panel game controls preserve the single board and auth gates."""
from pathlib import Path
from unittest.mock import Mock

import pytest

import web_panel_game_control_api as api


@pytest.mark.parametrize("path", [
    "/api/step", "/api/run", "/api/stop", "/api/reset",
])
def test_exact_control_routes_delegate_once(path):
    handler = object()
    callback = Mock()
    assert api.dispatch_panel_game_control_post(
        handler, path, control_action=callback) is True
    callback.assert_called_once_with(handler, path)


@pytest.mark.parametrize("path", [
    "/api/remote-workers/step",
    "/api/step/extra",
    "/api/reset-extra",
    "/api/run-now",
    "/api/state",
])
def test_non_panel_routes_cannot_mutate_game(path):
    callback = Mock()
    assert not api.dispatch_panel_game_control_post(
        object(), path, control_action=callback)
    callback.assert_not_called()


def test_panel_session_and_origin_checks_precede_mutating_dispatch():
    server = (Path(__file__).resolve().parents[1] / "server.py").read_text(
        encoding="utf-8")
    post = server.split("    def do_POST(self):", 1)[1].split(
        "if __name__ == '__main__':", 1)[0]
    auth = post.index("if not self.authenticated_user():")
    origin = post.index("if not arena_auth.origin_allowed(")
    mutate = post.index(
        "web_panel_game_control_api.dispatch_panel_game_control_post(")
    assert auth < origin < mutate
    assert post.index("worker_backend.dispatch_worker_post(") < auth
    assert post.index("web_panel_auth.dispatch_panel_auth_post(") < auth


def test_authoritative_state_mutation_retains_original_lock_and_fields():
    root = Path(__file__).resolve().parents[1]
    source = (root / "server.py").read_text(encoding="utf-8")
    action = source.split("def _panel_game_control_action(", 1)[1].split(
        "class Handler(BaseHTTPRequestHandler):", 1)[0]
    assert action.count("with lock:") == 5  # step + error, run, stop, reset
    assert "do_step()" in action
    assert "threading.Thread(target=autoplay_loop, daemon=True).start()" in action
    assert "board = chess.Board()" in action
    for field in ("moves = []", "timings = []", "illegal_moves = 0",
                  "started_at = time.time()", "last_error = ''",
                  "last_move = None"):
        assert field in action
    assert "handler.send_json({'error': str(e), **state()}, 422)" in action


def test_adapter_never_owns_game_state_or_worker_tokens():
    module = (Path(__file__).resolve().parents[1] / "web_panel_game_control_api.py").read_text(
        encoding="utf-8")
    assert "chess.Board" not in module
    assert "threading.Thread" not in module
    assert "WorkerTokenStore" not in module
    assert "sqlite3.connect" not in module
