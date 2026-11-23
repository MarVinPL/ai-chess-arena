"""Route isolation regressions for Issue #314: panel cookies != Worker tokens."""
import io
from pathlib import Path
from unittest.mock import patch

import server
import web_panel_backend
import worker_backend
import web_panel_auth


class HandlerStub:
    def __init__(self, payload=b"", headers=None):
        self.headers = headers or {}
        self.rfile = io.BytesIO(payload)
        self.wfile = io.BytesIO()
        self.responses = []
        self.error = None

    def send_json(self, data, code=200, extra_headers=()):
        self.responses.append((code, data))

    def send_error(self, code):
        self.error = code

    def send_response(self, code):
        self.responses.append((code, None))

    def send_header(self, *_):
        pass

    def end_headers(self):
        pass


def test_worker_router_does_not_accept_admin_cookie_for_authentication(tmp_path):
    handler = HandlerStub(
        b'{"worker_id":"android_12345678","token":"invalid"}',
        {"Cookie": "arena_session=admin", "Content-Length": "54"},
    )
    # Invalid JSON length or token is denied; panel session is never examined.
    assert worker_backend.dispatch_worker_post(
        handler, worker_backend.AUTH_ROUTE, tmp_path / "tokens.sqlite", {}
    )
    assert handler.responses[-1][0] in (400, 401)
    assert not handler.responses[-1][1].get("authenticated", False)


def test_disabled_worker_api_is_not_authorized_by_panel_session(tmp_path, monkeypatch):
    monkeypatch.delenv("ARENA_PHONE_WORKER_API_ENABLED", raising=False)
    h = HandlerStub(headers={"Cookie": "arena_session=admin"})
    assert worker_backend.dispatch_worker_post(
        h, "/api/remote-workers/job-poll", tmp_path / "tokens.sqlite", {}
    )
    assert h.responses == [(503, {"error": "Android Worker API nie jest jeszcze włączone"})]


def test_unknown_worker_route_never_bypasses_panel_routing(tmp_path):
    h = HandlerStub(headers={"Cookie": "arena_session=admin"})
    assert not worker_backend.dispatch_worker_post(
        h, "/api/remote-workers/admin-assign", tmp_path / "tokens.sqlite", {}
    )
    assert not h.responses


def test_panel_asset_router_allowlist_and_existing_handler_contract():
    root = Path(server.__file__).resolve().parent
    for path in ("/assets/arena.css", "/assets/arena.js"):
        h = HandlerStub()
        server.Handler.send_panel_asset(h, path)
        assert h.responses[0][0] == 200
        assert h.wfile.getvalue() == (root / web_panel_backend.ASSETS[path][0]).read_bytes()
    bad = HandlerStub()
    server.Handler.send_panel_asset(bad, "/assets/../server.py")
    assert bad.error == 404
    assert bad.wfile.getvalue() == b""


def test_composition_root_preserves_both_authentication_boundaries():
    source = Path(server.__file__).read_text(encoding="utf-8")
    post = source.split("    def do_POST(self):", 1)[1]
    assert post.index("worker_backend.dispatch_worker_post(") < post.index(
        "web_panel_auth.dispatch_panel_auth_post("
    )
    assert post.index("worker_backend.dispatch_worker_post(") < post.index(
        "if not self.authenticated_user():"
    )
    get = source.split("    def do_GET(self):", 1)[1]
    assert get.index("if not user:") < get.index("self.send_panel_asset(path)")
    assert "WorkerTokenStore" in Path(worker_backend.__file__).read_text()
    assert "session_user" not in Path(worker_backend.__file__).read_text()
    assert "WorkerTokenStore" not in Path(web_panel_backend.__file__).read_text()


def test_panel_auth_isolated_from_machine_token():
    """No Worker token or route is accepted by browser-session auth."""
    h = HandlerStub(headers={"Authorization": "Bearer fake-worker-token"})
    assert not web_panel_auth.dispatch_panel_auth_post(
        h, "/api/remote-workers/authenticate"
    )
    assert not h.responses


def test_browser_login_rejects_origin_before_reading_credentials():
    h = HandlerStub(headers={"Origin": "https://untrusted.invalid", "Content-Length": "2"})
    with patch.object(web_panel_auth.arena_auth, "is_configured", return_value=True), (
        patch.object(web_panel_auth.arena_auth, "origin_allowed", return_value=False)
    ):
        assert web_panel_auth.dispatch_panel_auth_post(h, "/api/auth/login")
    assert h.responses[0][0] == 403


def test_browser_login_keeps_rate_limit_and_cookie_policy():
    body = b'{"username":"operator","password":"example"}'
    h = HandlerStub(body, {"Content-Length": str(len(body))})
    h.client_address = ("127.0.0.1", 12345)
    with patch.object(web_panel_auth.arena_auth, "is_configured", return_value=True), (
        patch.object(web_panel_auth.arena_auth, "origin_allowed", return_value=True)
    ), patch.object(web_panel_auth.arena_auth, "client_key", return_value="test"), (
        patch.object(web_panel_auth.arena_auth, "check_login", return_value=(False, "rate_limited"))
    ):
        assert web_panel_auth.dispatch_panel_auth_post(h, "/api/auth/login")
    assert h.responses[-1][0] == 429

    h = HandlerStub(body, {"Content-Length": str(len(body))})
    h.client_address = ("127.0.0.1", 12345)
    with patch.object(web_panel_auth.arena_auth, "is_configured", return_value=True), (
        patch.object(web_panel_auth.arena_auth, "origin_allowed", return_value=True)
    ), patch.object(web_panel_auth.arena_auth, "client_key", return_value="test"), (
        patch.object(web_panel_auth.arena_auth, "check_login", return_value=(True, "ok"))
    ), patch.object(web_panel_auth.arena_auth, "create_session_token", return_value="test-session"), (
        patch.object(web_panel_auth.arena_auth, "session_cookie", return_value="arena_session=test")
    ):
        assert web_panel_auth.dispatch_panel_auth_post(h, "/api/auth/login")
    assert h.responses[-1][0] == 200
    assert h.responses[-1][1] == {"ok": True}


def test_browser_logout_requires_origin_and_sets_expired_cookie():
    h = HandlerStub(headers={"Origin": "https://untrusted.invalid"})
    with patch.object(web_panel_auth.arena_auth, "origin_allowed", return_value=False):
        assert web_panel_auth.dispatch_panel_auth_post(h, "/api/auth/logout")
    assert h.responses[-1][0] == 403

    h = HandlerStub()
    with patch.object(web_panel_auth.arena_auth, "origin_allowed", return_value=True), (
        patch.object(web_panel_auth.arena_auth, "expired_cookie", return_value="expired")
    ):
        assert web_panel_auth.dispatch_panel_auth_post(h, "/api/auth/logout")
    assert h.responses[-1] == (200, {"ok": True})
