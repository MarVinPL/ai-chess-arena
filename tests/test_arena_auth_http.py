import json
import os
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch

import server


ORIGIN = "https://arena.148-113-182-137.sslip.io"


class ArenaAuthHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.old_env = {key: os.environ.get(key) for key in (
            "ARENA_AUTH_USERNAME", "ARENA_AUTH_PASSWORD_HASH",
            "ARENA_AUTH_SESSION_SECRET", "ARENA_AUTH_ORIGIN")}
        os.environ.update({
            "ARENA_AUTH_USERNAME": "arena",
            "ARENA_AUTH_PASSWORD_HASH": server.arena_auth.hash_password("test-only-password-for-arena"),
            "ARENA_AUTH_SESSION_SECRET": server.arena_auth._b64e(b"test-only-session-secret-value-123456789"),
            "ARENA_AUTH_ORIGIN": ORIGIN,
        })
        cls.http = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.thread = threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.http.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()
        cls.thread.join(timeout=2)
        for key, value in cls.old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def request(self, path, method="GET", payload=None, cookie=None, origin=ORIGIN):
        headers = {}
        body = None
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if cookie:
            headers["Cookie"] = cookie
        if origin:
            headers["Origin"] = origin
        request = urllib.request.Request(self.base + path, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, response.read(), response.headers
        except urllib.error.HTTPError as error:
            return error.code, error.read(), error.headers

    def test_unauthenticated_root_shows_login_form_and_api_is_protected(self):
        status, body, _ = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn(b"Zaloguj si", body)
        api_status, api_body, _ = self.request("/api/state")
        self.assertEqual(api_status, 401)
        self.assertIn(b"/login", api_body)

    def test_login_sets_secure_session_cookie_and_status_recognizes_it(self):
        status, body, headers = self.request(
            "/api/auth/login", method="POST",
            payload={"username": "arena", "password": "test-only-password-for-arena"})
        self.assertEqual(status, 200, body.decode("utf-8"))
        cookie_header = headers.get("Set-Cookie")
        self.assertIsNotNone(cookie_header)
        self.assertIn("HttpOnly", cookie_header)
        self.assertIn("Secure", cookie_header)
        self.assertIn("SameSite=Strict", cookie_header)
        cookie = cookie_header.split(";", 1)[0]
        status, body, _ = self.request("/api/auth/status", cookie=cookie)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"authenticated": True, "username": "arena"})

    def test_login_rejects_wrong_origin(self):
        status, _, _ = self.request(
            "/api/auth/login", method="POST",
            payload={"username": "arena", "password": "test-only-password-for-arena"},
            origin="https://attacker.example")
        self.assertEqual(status, 403)

    def test_unauthenticated_post_cannot_reach_application_routes(self):
        status, _, _ = self.request("/api/stop", method="POST", payload={})
        self.assertEqual(status, 401)

    def test_logout_expires_session_cookie(self):
        status, _, headers = self.request("/api/auth/logout", method="POST", payload={})
        self.assertEqual(status, 200)
        self.assertIn("Max-Age=0", headers.get("Set-Cookie", ""))


if __name__ == "__main__":
    unittest.main()
