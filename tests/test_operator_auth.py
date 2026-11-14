import tempfile
import unittest
from pathlib import Path

import operator_auth


class OperatorAuthConfigTests(unittest.TestCase):
    def test_requires_explicit_https_origin_and_rp_id(self):
        for env in (
            {},
            {"ARENA_WEBAUTHN_RP_ID": "arena.example.test"},
            {"ARENA_WEBAUTHN_ORIGIN": "https://arena.example.test"},
            {"ARENA_WEBAUTHN_RP_ID": "127.0.0.1", "ARENA_WEBAUTHN_ORIGIN": "https://127.0.0.1"},
            {"ARENA_WEBAUTHN_RP_ID": "arena.example.test", "ARENA_WEBAUTHN_ORIGIN": "http://arena.example.test"},
            {"ARENA_WEBAUTHN_RP_ID": "arena.example.test", "ARENA_WEBAUTHN_ORIGIN": "https://evil.test"},
            {"ARENA_WEBAUTHN_RP_ID": "arena.example.test", "ARENA_WEBAUTHN_ORIGIN": "https://arena.example.test/path"},
        ):
            with self.subTest(env=env), self.assertRaises(operator_auth.OperatorAuthError):
                operator_auth.load_config(env)

    def test_accepts_exact_https_origin_or_subdomain(self):
        cfg = operator_auth.load_config({
            "ARENA_WEBAUTHN_RP_ID": "example.test",
            "ARENA_WEBAUTHN_ORIGIN": "https://arena.example.test/",
        })
        self.assertEqual(cfg.rp_id, "example.test")
        self.assertEqual(cfg.origin, "https://arena.example.test")


class OperatorAuthStorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "arena.sqlite3"

    def tearDown(self):
        self.tmp.cleanup()

    def test_owner_enrollment_stores_only_hash_and_is_single_pending(self):
        token = operator_auth.create_owner_enrollment(self.db, "Arena Owner")
        self.assertGreaterEqual(len(token), 32)
        with operator_auth._connect(self.db) as conn:
            row = conn.execute("SELECT token_hash,role,display_name FROM arena_operator_enrollments").fetchone()
            self.assertEqual(row["role"], "owner")
            self.assertEqual(row["display_name"], "Arena Owner")
            self.assertNotEqual(row["token_hash"], token)
            with self.assertRaises(operator_auth.OperatorAuthError):
                operator_auth.create_owner_enrollment(self.db, "Second Owner")

    def test_registration_options_bind_random_user_handle_and_expiring_challenge(self):
        token = operator_auth.create_owner_enrollment(self.db, "Arena Owner")
        cfg = operator_auth.WebAuthnConfig("example.test", "https://arena.example.test")
        options = operator_auth.begin_registration(self.db, cfg, token)
        self.assertIn("ceremony_id", options)
        self.assertIn("challenge", options)
        self.assertEqual(options["authenticatorSelection"]["residentKey"], "required")
        self.assertEqual(options["authenticatorSelection"]["userVerification"], "required")
        with operator_auth._connect(self.db) as conn:
            row = conn.execute(
                "SELECT purpose,challenge,user_handle,expires_at FROM arena_auth_challenges WHERE ceremony_id=?",
                (options["ceremony_id"],),
            ).fetchone()
        self.assertEqual(row["purpose"], "registration")
        self.assertEqual(len(row["user_handle"]), 32)
        self.assertGreater(row["expires_at"], row["created_at"] if "created_at" in row.keys() else 0)

    def test_operator_invite_requires_specific_active_owner(self):
        with operator_auth._connect(self.db) as conn:
            operator_auth.ensure_schema(conn)
            conn.execute(
                "INSERT INTO arena_operators(operator_id,display_name,role,user_handle,created_at) VALUES(?,?,?,?,?)",
                ("owner-id", "Owner", "owner", b"o" * 32, 1.0),
            )
        with self.assertRaises(operator_auth.OperatorAuthError):
            operator_auth.create_operator_invitation(self.db, "not-owner", "Operator")
        token = operator_auth.create_operator_invitation(self.db, "owner-id", "Operator")
        self.assertGreaterEqual(len(token), 32)

    def test_sessions_are_hashed_csrf_checked_idle_expired_and_revocable(self):
        with operator_auth._connect(self.db) as conn:
            operator_auth.ensure_schema(conn)
            conn.execute(
                "INSERT INTO arena_operators(operator_id,display_name,role,user_handle,created_at) VALUES(?,?,?,?,?)",
                ("owner-id", "Owner", "owner", b"o" * 32, 1.0),
            )
            session_token, csrf_token = operator_auth._new_session(conn, "owner-id", 1000.0)
        session = operator_auth.get_session(self.db, session_token, now=1001.0)
        self.assertEqual(session["role"], "owner")
        self.assertTrue(operator_auth.verify_csrf(session, csrf_token))
        self.assertFalse(operator_auth.verify_csrf(session, "wrong-token"))
        self.assertIsNone(operator_auth.get_session(self.db, session_token, now=1000.0 + operator_auth.SESSION_IDLE_SECONDS))
        self.assertTrue(operator_auth.revoke_session(self.db, session_token))
        self.assertIsNone(operator_auth.get_session(self.db, session_token, now=1002.0))


if __name__ == "__main__":
    unittest.main()
