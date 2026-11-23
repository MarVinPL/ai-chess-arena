"""Regression coverage for one authoritative Worker authenticate route (#295).

The legacy inline Worker API remains in server.py on this stacked Draft.
A later #314 integration must migrate it into worker_backend.py without
reintroducing duplicate machine authentication or browser-session fallback.
"""
import io
from pathlib import Path
from unittest.mock import patch

import server


class HandlerStub:
    def __init__(self, payload):
        self.headers = {"Content-Length": str(len(payload)), "Cookie": "arena_session=admin"}
        self.rfile = io.BytesIO(payload)
        self.responses = []

    def send_json(self, value, status=200, *_):
        self.responses.append((status, value))


def test_one_worker_authentication_handler_in_http_entrypoint():
    source = Path(server.__file__).read_text(encoding="utf-8")
    post = source.split("    def do_POST(self):", 1)[1]
    assert post.count("if path == '/api/remote-workers/authenticate':") == 1
    assert post.index("if path == '/api/remote-workers/authenticate':") < post.index(
        "if path == '/api/auth/login':")
    assert "WorkerTokenStore(DB).authenticate(worker_id, token)" in post


def test_worker_authentication_ignores_admin_cookie_and_rejects_invalid_token():
    payload = b'{"worker_id":"worker_12345678","token":"invalid"}'
    handler = HandlerStub(payload)
    with patch.object(server.remote_worker_tokens.WorkerTokenStore, "authenticate",
                      return_value=False) as authenticate:
        # Run the real HTTP dispatch against an in-memory handler stub.
        with patch.object(server, "_route_path", return_value="/api/remote-workers/authenticate"):
            server.Handler.do_POST(handler)
    authenticate.assert_called_once_with("worker_12345678", "invalid")
    assert handler.responses == [(401, {"authenticated": False})]


def test_authentication_rejects_malformed_json_even_with_admin_cookie():
    handler = HandlerStub(b'{')
    with patch.object(server, "_route_path", return_value="/api/remote-workers/authenticate"):
        server.Handler.do_POST(handler)
    assert handler.responses == [(400, {"error": "Nieprawidłowe żądanie"})]
