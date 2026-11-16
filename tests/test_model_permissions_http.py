"""HTTP boundary tests for model-permission routes."""
import json
import os
import sqlite3
import tempfile
import threading
import urllib.error
import urllib.request
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch

import server


ORIGIN = "https://arena.148-113-182-137.sslip.io"


class ModelPermissionHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.old_env = {key: os.environ.get(key) for key in (
            "ARENA_AUTH_USERNAME", "ARENA_AUTH_PASSWORD_HASH",
            "ARENA_AUTH_SESSION_SECRET", "ARENA_AUTH_ORIGIN")}
        os.environ.update({
            "ARENA_AUTH_USERNAME": "arena",
            "ARENA_AUTH_PASSWORD_HASH": server.arena_auth.hash_password("http-test-password-long-enough"),
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

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = self.tempdir.name + "/permissions.sqlite3"

        def connect():
            db = sqlite3.connect(self.db_path, timeout=10)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            return db

        self.connect_patch = patch.object(server.league, "connect", side_effect=connect)
        self.connect_patch.start()
        token = server.arena_auth.create_session_token("arena")
        self.cookie = f"{server.arena_auth.COOKIE_NAME}={token}"

    def tearDown(self):
        self.connect_patch.stop()
        self.tempdir.cleanup()

    def request(self, path, method="GET", payload=None, authenticated=True, origin=ORIGIN):
        headers = {}
        body = None
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if authenticated:
            headers["Cookie"] = self.cookie
        if origin:
            headers["Origin"] = origin
        request = urllib.request.Request(self.base + path, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    def request_payload(self):
        return {
            "model_id": 987654, "repo_id": "org/test-model",
            "revision": "a" * 40,
            "artifact_sha256": "b" * 64,
            "scope": "TEST_ONLY", "reason": "HTTP boundary test",
            "confirm": True,
        }

    def test_permission_routes_require_authenticated_session(self):
        status, body = self.request("/api/model-permissions", authenticated=False)
        self.assertEqual(status, 401)
        self.assertIn("Wymagane logowanie", body["error"])

    def test_permission_mutations_reject_untrusted_origin(self):
        status, body = self.request(
            "/api/model-permissions/request", method="POST",
            payload=self.request_payload(), origin="https://attacker.example")
        self.assertEqual(status, 403)
        self.assertIn("spoza dozwolonej domeny", body["error"])

    def test_request_and_history_are_exposed_only_after_session_auth(self):
        status, created = self.request(
            "/api/model-permissions/request", method="POST",
            payload=self.request_payload())
        self.assertEqual(status, 201)
        self.assertEqual(created["status"], "pending")
        status, rows = self.request("/api/model-permissions")
        self.assertEqual(status, 200)
        self.assertEqual(rows[0]["requested_by"], "arena")
        self.assertEqual([event["event"] for event in rows[0]["history"]], ["requested"])

    def test_approval_is_authenticated_and_appends_audit_event(self):
        _, created = self.request(
            "/api/model-permissions/request", method="POST",
            payload=self.request_payload())
        status, approved = self.request(
            "/api/model-permissions/decision", method="POST",
            payload={"request_id": created["id"], "action": "approve", "confirm": True})
        self.assertEqual(status, 200)
        self.assertEqual(approved["status"], "approved")
        _, rows = self.request("/api/model-permissions")
        self.assertEqual(
            [event["event"] for event in rows[0]["history"]],
            ["requested", "approved"],
        )


if __name__ == "__main__":
    unittest.main()
