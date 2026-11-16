import os
import unittest
from unittest.mock import patch

import arena_auth


class ArenaAuthTests(unittest.TestCase):
    def setUp(self):
        self.password = "correct-horse-battery-2026"
        self.username = "arena"
        self.password_hash = arena_auth.hash_password(self.password)
        self.environment = {
            "ARENA_AUTH_USERNAME": self.username,
            "ARENA_AUTH_PASSWORD_HASH": self.password_hash,
            "ARENA_AUTH_SESSION_SECRET": arena_auth._b64e(b"s" * 48),
            "ARENA_AUTH_ORIGIN": "https://arena.148-113-182-137.sslip.io",
        }
        self.env_patch = patch.dict(os.environ, self.environment, clear=True)
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)

    def test_correct_credentials_create_a_valid_session(self):
        ok, reason = arena_auth.check_login(self.username, self.password, "127.0.0.1")
        self.assertTrue(ok)
        self.assertEqual(reason, "ok")
        token = arena_auth.create_session_token(self.username, now=1_800_000_000)
        cookie = f"{arena_auth.COOKIE_NAME}={token}"
        self.assertEqual(arena_auth.session_user(cookie, now=1_800_000_001), self.username)

    def test_invalid_credentials_are_rejected(self):
        ok, reason = arena_auth.check_login(self.username, "wrong-password-value", "127.0.0.2")
        self.assertFalse(ok)
        self.assertEqual(reason, "invalid_credentials")

    def test_session_signature_tampering_is_rejected(self):
        token = arena_auth.create_session_token(self.username, now=1_800_000_000)
        cookie = f"{arena_auth.COOKIE_NAME}={token[:-1]}x"
        self.assertIsNone(arena_auth.session_user(cookie, now=1_800_000_001))

    def test_expired_session_is_rejected(self):
        token = arena_auth.create_session_token(self.username, now=1_800_000_000)
        cookie = f"{arena_auth.COOKIE_NAME}={token}"
        self.assertIsNone(arena_auth.session_user(cookie, now=1_800_000_000 + arena_auth.SESSION_TTL_SECONDS + 1))

    def test_cookie_is_http_only_secure_and_same_site_strict(self):
        value = arena_auth.session_cookie("opaque")
        self.assertIn("HttpOnly", value)
        self.assertIn("Secure", value)
        self.assertIn("SameSite=Strict", value)

    def test_origin_must_match_exact_configured_origin(self):
        self.assertTrue(arena_auth.origin_allowed("https://arena.148-113-182-137.sslip.io"))
        self.assertFalse(arena_auth.origin_allowed("https://attacker.example"))

    def test_fifth_bad_attempt_is_allowed_but_sixth_is_rate_limited(self):
        for _ in range(arena_auth.LOGIN_MAX_FAILURES):
            ok, reason = arena_auth.check_login(self.username, "incorrect-password", "limited-client")
            self.assertFalse(ok)
            self.assertEqual(reason, "invalid_credentials")
        ok, reason = arena_auth.check_login(self.username, "incorrect-password", "limited-client")
        self.assertFalse(ok)
        self.assertEqual(reason, "rate_limited")

    def test_rate_limit_resets_after_window(self):
        for _ in range(arena_auth.LOGIN_MAX_FAILURES):
            arena_auth.record_failure("test-client", now=1000)
        self.assertFalse(arena_auth.login_allowed("test-client", now=1001))
        self.assertTrue(arena_auth.login_allowed("test-client", now=1000 + arena_auth.LOGIN_WINDOW_SECONDS + 1))

    def test_client_key_uses_last_forwarded_address(self):
        self.assertEqual(arena_auth.client_key("198.51.100.9, 203.0.113.8", "127.0.0.1"), "203.0.113.8")

    def test_client_key_falls_back_to_peer_address(self):
        self.assertEqual(arena_auth.client_key("", "127.0.0.1"), "127.0.0.1")

    def test_short_password_hash_is_rejected(self):
        with self.assertRaises(ValueError):
            arena_auth.hash_password("too-short")


class ArenaAuthFailClosedTests(unittest.TestCase):
    def test_missing_configuration_rejects_login(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(arena_auth.is_configured())
            ok, reason = arena_auth.check_login("arena", "anything", "127.0.0.1")
            self.assertFalse(ok)
            self.assertEqual(reason, "not_configured")
            self.assertIsNone(arena_auth.session_user("arena_session=forged"))


if __name__ == "__main__":
    unittest.main()
