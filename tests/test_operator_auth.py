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
        self.assertIsNone(operator_auth.get_session(self.db, session_token, now=1001.0 + operator_auth.SESSION_IDLE_SECONDS))
        self.assertTrue(operator_auth.revoke_session(self.db, session_token))
        self.assertIsNone(operator_auth.get_session(self.db, session_token, now=1002.0))


    def test_verified_handler_binding_requires_exact_origin_and_csrf(self):
        import time
        from types import SimpleNamespace
        now = time.time()
        with operator_auth._connect(self.db) as conn:
            operator_auth.ensure_schema(conn)
            conn.execute(
                "INSERT INTO arena_operators(operator_id,display_name,role,user_handle,created_at) VALUES(?,?,?,?,?)",
                ("owner-id", "Owner", "owner", b"o" * 32, now),
            )
            session_token, csrf_token = operator_auth._new_session(conn, "owner-id", now)
        config = operator_auth.WebAuthnConfig("example.test", "https://arena.example.test")
        valid = SimpleNamespace(headers={
            "Cookie": "arena_session=" + session_token,
            "Origin": config.origin,
            "X-Arena-CSRF": csrf_token,
        })
        session = operator_auth.bind_verified_request(self.db, config, valid, require_csrf=True)
        self.assertEqual(session["role"], "owner")
        self.assertEqual(valid._arena_verified_actor, "owner-id")
        invalid_origin = SimpleNamespace(headers={
            "Cookie": "arena_session=" + session_token,
            "Origin": "https://attacker.example.test",
            "X-Arena-CSRF": csrf_token,
        })
        self.assertIsNone(operator_auth.bind_verified_request(
            self.db, config, invalid_origin, require_csrf=True))
        invalid_csrf = SimpleNamespace(headers={
            "Cookie": "arena_session=" + session_token,
            "Origin": config.origin,
            "X-Arena-CSRF": "wrong",
        })
        self.assertIsNone(operator_auth.bind_verified_request(
            self.db, config, invalid_csrf, require_csrf=True))


    def test_discoverable_login_uses_stored_user_handle_and_creates_session(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from webauthn.helpers import bytes_to_base64url
        now = operator_auth.time.time()
        credential_id = b"credential-id"
        user_handle = b"operator-user-handle-32-bytes!!!"
        challenge = b"fresh-login-challenge"
        with operator_auth._db(self.db) as conn:
            operator_auth.ensure_schema(conn)
            conn.execute(
                "INSERT INTO arena_operators(operator_id,display_name,role,user_handle,created_at) VALUES(?,?,?,?,?)",
                ("owner-id", "Owner", "owner", user_handle, now),
            )
            conn.execute(
                """INSERT INTO arena_webauthn_credentials
                (credential_id,operator_id,public_key,sign_count,created_at)
                VALUES(?,?,?,?,?)""",
                (bytes_to_base64url(credential_id), "owner-id", b"public-key", 0, now),
            )
            conn.execute(
                """INSERT INTO arena_auth_challenges
                (ceremony_id,purpose,challenge,created_at,expires_at)
                VALUES(?,?,?,?,?)""",
                ("login-ceremony", "authentication", challenge, now, now + 120),
            )
        credential = {
            "id": bytes_to_base64url(credential_id),
            "rawId": bytes_to_base64url(credential_id),
            "type": "public-key",
            "response": {"userHandle": bytes_to_base64url(user_handle)},
        }
        verified = SimpleNamespace(
            credential_id=credential_id, new_sign_count=1,
            credential_device_type="single_device", credential_backed_up=False,
        )
        with patch.object(operator_auth, "verify_authentication_response", return_value=verified) as verify:
            result = operator_auth.finish_authentication(
                self.db, operator_auth.WebAuthnConfig("example.test", "https://arena.example.test"),
                "login-ceremony", credential,
            )
        verify.assert_called_once()
        self.assertEqual(verify.call_args.kwargs["credential_public_key"], b"public-key")
        self.assertEqual(result["operator"]["operator_id"], "owner-id")
        self.assertEqual(result["operator"]["role"], "owner")
        session = operator_auth.get_session(self.db, result["session_token"])
        self.assertEqual(session["operator_id"], "owner-id")
        self.assertTrue(operator_auth.verify_csrf(session, result["csrf_token"]))
        with operator_auth._db(self.db) as conn:
            stored = conn.execute(
                "SELECT consumed_at FROM arena_auth_challenges WHERE ceremony_id='login-ceremony'"
            ).fetchone()
            self.assertIsNotNone(stored["consumed_at"])



    def test_revoked_credential_is_rejected_before_webauthn_verification(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from webauthn.helpers import bytes_to_base64url
        now = operator_auth.time.time()
        credential_id = b"revoked-credential"
        user_handle = b"revoked-user-handle-32-bytes!!!!"
        challenge = b"revoked-login-challenge"
        encoded_id = bytes_to_base64url(credential_id)
        with operator_auth._db(self.db) as conn:
            operator_auth.ensure_schema(conn)
            conn.execute(
                "INSERT INTO arena_operators(operator_id,display_name,role,user_handle,created_at) VALUES(?,?,?,?,?)",
                ("owner-id", "Owner", "owner", user_handle, now),
            )
            conn.execute(
                """INSERT INTO arena_webauthn_credentials
                (credential_id,operator_id,public_key,sign_count,created_at,revoked_at)
                VALUES(?,?,?,?,?,?)""",
                (encoded_id, "owner-id", b"public-key", 0, now, now - 1),
            )
            conn.execute(
                """INSERT INTO arena_auth_challenges
                (ceremony_id,purpose,challenge,created_at,expires_at)
                VALUES(?,?,?,?,?)""",
                ("revoked-ceremony", "authentication", challenge, now, now + 120),
            )
        credential = {
            "id": encoded_id, "rawId": encoded_id, "type": "public-key",
            "response": {"userHandle": bytes_to_base64url(user_handle)},
        }
        with patch.object(operator_auth, "verify_authentication_response") as verify:
            with self.assertRaises(operator_auth.OperatorAuthError):
                operator_auth.finish_authentication(
                    self.db,
                    operator_auth.WebAuthnConfig("example.test", "https://arena.example.test"),
                    "revoked-ceremony",
                    credential,
                )
        verify.assert_not_called()


    def test_persistent_rate_limit_counts_attempts_and_resets_window(self):
        operator_auth.consume_rate_limit(self.db, "login:ip:192.0.2.10", 2, window_seconds=60, now=1000)
        operator_auth.consume_rate_limit(self.db, "login:ip:192.0.2.10", 2, window_seconds=60, now=1001)
        with self.assertRaises(operator_auth.OperatorRateLimitError) as caught:
            operator_auth.consume_rate_limit(self.db, "login:ip:192.0.2.10", 2, window_seconds=60, now=1002)
        self.assertEqual(caught.exception.retry_after, 58)
        operator_auth.consume_rate_limit(self.db, "login:ip:192.0.2.10", 2, window_seconds=60, now=1060)
        with operator_auth._db(self.db) as conn:
            row = conn.execute("SELECT bucket_hash FROM arena_auth_rate_limits").fetchone()
        self.assertEqual(len(row["bucket_hash"]), 64)
        self.assertNotEqual(row["bucket_hash"], "login:ip:192.0.2.10")

    def test_owner_can_revoke_credential_and_all_target_sessions_but_not_last_owner_key(self):
        from webauthn.helpers import bytes_to_base64url
        now = operator_auth.time.time()
        owner_credential = bytes_to_base64url(b"owner-credential")
        operator_credential = bytes_to_base64url(b"operator-credential")
        with operator_auth._db(self.db) as conn:
            operator_auth.ensure_schema(conn)
            conn.execute(
                "INSERT INTO arena_operators(operator_id,display_name,role,user_handle,created_at) VALUES(?,?,?,?,?)",
                ("owner-id", "Owner", "owner", b"o" * 32, now),
            )
            conn.execute(
                "INSERT INTO arena_operators(operator_id,display_name,role,user_handle,created_at) VALUES(?,?,?,?,?)",
                ("operator-id", "Operator", "operator", b"p" * 32, now),
            )
            for cid, oid in ((owner_credential, "owner-id"), (operator_credential, "operator-id")):
                conn.execute(
                    """INSERT INTO arena_webauthn_credentials
                    (credential_id,operator_id,public_key,sign_count,created_at)
                    VALUES(?,?,?,?,?)""",
                    (cid, oid, b"public-key", 0, now),
                )
            operator_session, _ = operator_auth._new_session(conn, "operator-id", now)
        with self.assertRaises(operator_auth.OperatorAuthError):
            operator_auth.revoke_credential(self.db, "owner-id", owner_credential)
        result = operator_auth.revoke_credential(self.db, "owner-id", operator_credential)
        self.assertEqual(result["operator_id"], "operator-id")
        self.assertIsNone(operator_auth.get_session(self.db, operator_session))
        inventory = operator_auth.list_operators(self.db)
        target = next(item for item in inventory if item["operator_id"] == "operator-id")
        self.assertEqual(target["active_credentials"], 0)
        events = operator_auth.list_operator_events(self.db)
        self.assertEqual(events[0]["action"], "credential_revoked")
        self.assertEqual(events[0]["actor_operator_id"], "owner-id")
        self.assertEqual(events[0]["target_operator_id"], "operator-id")

    def test_owner_can_suspend_operator_and_revoke_all_sessions(self):
        now = operator_auth.time.time()
        with operator_auth._db(self.db) as conn:
            operator_auth.ensure_schema(conn)
            for oid, role in (("owner-id", "owner"), ("operator-id", "operator")):
                conn.execute(
                    "INSERT INTO arena_operators(operator_id,display_name,role,user_handle,created_at) VALUES(?,?,?,?,?)",
                    (oid, oid, role, (oid.encode() + b"x" * 32)[:32], now),
                )
            session_token, _ = operator_auth._new_session(conn, "operator-id", now)
        result = operator_auth.suspend_operator(self.db, "owner-id", "operator-id")
        self.assertTrue(result["sessions_revoked"])
        self.assertIsNone(operator_auth.get_session(self.db, session_token))
        with operator_auth._db(self.db) as conn:
            active = conn.execute(
                "SELECT active FROM arena_operators WHERE operator_id='operator-id'"
            ).fetchone()["active"]
        self.assertEqual(active, 0)
        events = operator_auth.list_operator_events(self.db)
        self.assertEqual(events[0]["action"], "operator_suspended")
        self.assertEqual(events[0]["actor_operator_id"], "owner-id")
        self.assertEqual(events[0]["target_operator_id"], "operator-id")

    def test_owner_cannot_suspend_self_or_last_active_owner(self):
        now = operator_auth.time.time()
        with operator_auth._db(self.db) as conn:
            operator_auth.ensure_schema(conn)
            for oid in ("owner-id", "other-owner"):
                conn.execute(
                    "INSERT INTO arena_operators(operator_id,display_name,role,user_handle,created_at) VALUES(?,?,?,?,?)",
                    (oid, oid, "owner", (oid.encode() + b"x" * 32)[:32], now),
                )
        with self.assertRaises(operator_auth.OperatorAuthError):
            operator_auth.suspend_operator(self.db, "owner-id", "owner-id")
        with self.assertRaises(operator_auth.OperatorAuthError):
            operator_auth.suspend_operator(self.db, "owner-id", "other-owner")


if __name__ == "__main__":
    unittest.main()
