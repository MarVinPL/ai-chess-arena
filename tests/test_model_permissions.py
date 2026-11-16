import hashlib
import sqlite3
import tempfile
import threading
import unittest

from model_permissions import (
    ensure_schema, request_permission, decide_permission, list_permissions, has_permission,
)

class ModelPermissionTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        ensure_schema(self.db)
        self.artifact_sha256 = hashlib.sha256(b"approved-gguf-fixture").hexdigest()
        self.ident = dict(model_id=1, repo_id="org/model", revision="a" * 40,
                          artifact_sha256=self.artifact_sha256)

    def tearDown(self):
        self.db.close()

    def request(self, scope="TEST_ONLY"):
        return request_permission(self.db, **self.ident, scope=scope,
                                  confirm=True, reason="kontrolowany test", actor="test-operator")

    def decide(self, **kwargs):
        return decide_permission(self.db, actor="test-operator", **kwargs)

    def approve(self, request):
        return self.decide(request_id=request["id"], action="approve", confirm=True)

    def test_mutation_requires_explicit_operator_identity(self):
        with self.assertRaisesRegex(ValueError, "tożsamości operatora"):
            request_permission(self.db, **self.ident, scope="TEST_ONLY",
                               confirm=True, reason="kontrolowany test")
        req = self.request()
        with self.assertRaisesRegex(ValueError, "tożsamości operatora"):
            decide_permission(self.db, request_id=req["id"], action="approve",
                              confirm=True)

    def test_schema_migration_supports_default_sqlite_tuple_rows(self):
        db = sqlite3.connect(":memory:")
        try:
            ensure_schema(db)
            ensure_schema(db)
            columns = {row[1] for row in db.execute("PRAGMA table_info(model_permission_requests)")}
            self.assertIn("artifact_sha256", columns)
        finally:
            db.close()

    def test_new_models_have_no_permissions_and_request_is_not_approval(self):
        self.assertFalse(has_permission(self.db, **self.ident, scope="TEST_ONLY"))
        req = self.request()
        self.assertEqual(req["status"], "pending")
        self.assertFalse(has_permission(self.db, **self.ident, scope="TEST_ONLY"))

    def test_explicit_approval_is_pinned_to_exact_revision(self):
        req = self.request()
        self.approve(req)
        self.assertTrue(has_permission(self.db, **self.ident, scope="TEST_ONLY"))
        self.assertFalse(has_permission(self.db, **{**self.ident, "revision": "b" * 40}, scope="TEST_ONLY"))
        self.assertFalse(has_permission(self.db, **{**self.ident, "artifact_sha256": "b" * 64}, scope="TEST_ONLY"))

    def test_evaluation_requires_separate_test_only_approval(self):
        with self.assertRaisesRegex(ValueError, "TEST_ONLY"):
            self.request("EVALUATION")
        test_req = self.request()
        self.approve(test_req)
        eval_req = self.request("EVALUATION")
        self.assertFalse(has_permission(self.db, **self.ident, scope="EVALUATION"))
        self.approve(eval_req)
        self.assertTrue(has_permission(self.db, **self.ident, scope="EVALUATION"))

    def test_pending_or_rejected_renewal_does_not_cancel_existing_grant(self):
        first = self.request()
        self.approve(first)
        renewal = self.request()
        self.assertTrue(has_permission(self.db, **self.ident, scope="TEST_ONLY"))
        self.decide(request_id=renewal["id"], action="reject",
                          confirm=True, reason="brak dodatkowej potrzeby")
        self.assertTrue(has_permission(self.db, **self.ident, scope="TEST_ONLY"))

    def test_revoking_newer_grant_preserves_older_approved_grant(self):
        first = self.request()
        self.approve(first)
        second = self.request()
        self.approve(second)

        self.decide(request_id=second["id"], action="revoke", confirm=True,
                    reason="wycofanie nowszej zgody")
        self.assertEqual(
            self.db.execute("SELECT status FROM model_permission_requests WHERE id=?",
                            (first["id"],)).fetchone()[0],
            "approved",
        )
        self.assertTrue(has_permission(self.db, **self.ident, scope="TEST_ONLY"))

        self.decide(request_id=first["id"], action="revoke", confirm=True,
                    reason="wycofanie ostatniej zgody")
        self.assertFalse(has_permission(self.db, **self.ident, scope="TEST_ONLY"))

    def test_repo_id_is_trimmed_before_storage_and_permission_lookup(self):
        request = request_permission(
            self.db, **{**self.ident, "repo_id": "  org/model  "},
            scope="TEST_ONLY", confirm=True, reason="kontrolowany test",
            actor="test-operator",
        )
        self.assertEqual(request["repo_id"], "org/model")
        self.approve(request)
        self.assertTrue(has_permission(self.db, **self.ident, scope="TEST_ONLY"))

    def test_tournament_is_never_granted_by_operational_permission_registry(self):
        with self.assertRaisesRegex(ValueError, "allowlist"):
            self.request("TOURNAMENT")

    def test_revoke_test_only_requires_evaluation_to_be_revoked_first(self):
        test_req = self.request()
        self.approve(test_req)
        eval_req = self.request("EVALUATION")
        self.approve(eval_req)
        with self.assertRaisesRegex(ValueError, "EVALUATION"):
            self.decide( request_id=test_req["id"], action="revoke",
                              confirm=True, reason="wycofanie")
        self.assertTrue(self.db.in_transaction, "failed decision must preserve the caller-owned transaction")
        self.decide( request_id=eval_req["id"], action="revoke",
                          confirm=True, reason="koniec ewaluacji")
        self.decide( request_id=test_req["id"], action="revoke",
                          confirm=True, reason="koniec testów")
        self.assertFalse(has_permission(self.db, **self.ident, scope="TEST_ONLY"))

    def test_reject_requires_reason_and_preserves_history(self):
        req = self.request()
        with self.assertRaisesRegex(ValueError, "uzasadnienie"):
            self.decide( request_id=req["id"], action="reject",
                              confirm=True, reason=" ")
        self.decide(request_id=req["id"], action="reject",
                    confirm=True, reason="brak dowodów")
        self.assertEqual([x["event"] for x in self.db.execute(
            "SELECT event FROM model_permission_events ORDER BY id")],
            ["requested", "rejected"])
        self.assertEqual(list_permissions(self.db)[0]["status"], "rejected")
        self.assertEqual(list_permissions(self.db)[0]["artifact_sha256"], self.artifact_sha256)

    def test_missing_confirmation_and_invalid_identity_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "potwierdzenie"):
            request_permission(self.db, **self.ident, scope="TEST_ONLY",
                               confirm=False, reason="kontrola", actor="test-operator")
        with self.assertRaisesRegex(ValueError, "SHA"):
            request_permission(self.db, **{**self.ident, "revision": "latest"},
                               scope="TEST_ONLY", confirm=True, reason="kontrola", actor="test-operator")

    def test_legacy_schema_is_migrated_before_pending_unique_index(self):
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        try:
            db.executescript("""
                CREATE TABLE model_permission_requests (
                    id INTEGER PRIMARY KEY, model_id INTEGER NOT NULL, repo_id TEXT NOT NULL,
                    revision TEXT NOT NULL, scope TEXT NOT NULL, status TEXT NOT NULL,
                    requested_by TEXT NOT NULL, requested_at TEXT NOT NULL, decided_by TEXT,
                    decided_at TEXT, reason TEXT NOT NULL DEFAULT '', snapshot_json TEXT NOT NULL
                );
                CREATE TABLE model_permission_events (
                    id INTEGER PRIMARY KEY, request_id INTEGER NOT NULL, model_id INTEGER NOT NULL,
                    repo_id TEXT NOT NULL, revision TEXT NOT NULL, scope TEXT NOT NULL,
                    event TEXT NOT NULL, actor TEXT NOT NULL, occurred_at TEXT NOT NULL,
                    reason TEXT NOT NULL DEFAULT '', snapshot_json TEXT NOT NULL
                );
            """)
            ensure_schema(db)
            request_columns = {row[1] for row in db.execute(
                "PRAGMA table_info(model_permission_requests)"
            )}
            event_columns = {row[1] for row in db.execute(
                "PRAGMA table_info(model_permission_events)"
            )}
            self.assertIn("artifact_sha256", request_columns)
            self.assertIn("artifact_sha256", event_columns)
            indexes = {row[1] for row in db.execute(
                "PRAGMA index_list(model_permission_requests)"
            )}
            self.assertIn("uq_model_permission_pending_identity", indexes)
        finally:
            db.close()

    def test_pending_request_is_unique_for_exact_identity(self):
        self.request()
        with self.assertRaisesRegex(ValueError, "oczekujące żądanie"):
            self.request()

    def test_audit_events_are_append_only_at_database_level(self):
        req = self.request()
        event_id = self.db.execute(
            "SELECT id FROM model_permission_events ORDER BY id LIMIT 1"
        ).fetchone()[0]
        with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
            self.db.execute(
                "UPDATE model_permission_events SET reason='tampered' WHERE id=?",
                (event_id,),
            )
        with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
            self.db.execute(
                "DELETE FROM model_permission_events WHERE id=?",
                (event_id,),
            )
        self.assertEqual(
            self.db.execute(
                "SELECT event FROM model_permission_events WHERE id=?", (event_id,)
            ).fetchone()[0],
            "requested",
        )

    def test_failed_owned_decision_rolls_back_its_transaction(self):
        with tempfile.NamedTemporaryFile(suffix=".sqlite3") as db_file:
            setup = sqlite3.connect(db_file.name, timeout=10)
            setup.row_factory = sqlite3.Row
            ensure_schema(setup)
            test_req = request_permission(
                setup, **self.ident, scope="TEST_ONLY", confirm=True,
                reason="initial test grant", actor="setup-operator",
            )
            decide_permission(
                setup, request_id=test_req["id"], action="approve", confirm=True,
                actor="setup-operator",
            )
            eval_req = request_permission(
                setup, **self.ident, scope="EVALUATION", confirm=True,
                reason="evaluation request", actor="setup-operator",
            )
            decide_permission(
                setup, request_id=eval_req["id"], action="approve", confirm=True,
                actor="setup-operator",
            )
            setup.commit()
            setup.close()

            connection = sqlite3.connect(db_file.name, timeout=10)
            connection.row_factory = sqlite3.Row
            with self.assertRaisesRegex(ValueError, "EVALUATION"):
                decide_permission(
                    connection, request_id=test_req["id"], action="revoke",
                    confirm=True, reason="end test", actor="test-operator",
                )
            self.assertFalse(connection.in_transaction)
            connection.close()

    def test_concurrent_evaluation_approval_and_test_only_revoke_preserve_dependency(self):
        with tempfile.NamedTemporaryFile(suffix=".sqlite3") as db_file:
            path = db_file.name
            setup = sqlite3.connect(path, timeout=10)
            setup.row_factory = sqlite3.Row
            ensure_schema(setup)
            test_req = request_permission(
                setup, **self.ident, scope="TEST_ONLY", confirm=True,
                reason="initial test grant", actor="setup-operator",
            )
            decide_permission(
                setup, request_id=test_req["id"], action="approve", confirm=True,
                actor="setup-operator",
            )
            eval_req = request_permission(
                setup, **self.ident, scope="EVALUATION", confirm=True,
                reason="evaluation request", actor="setup-operator",
            )
            setup.commit()
            setup.close()

            barrier = threading.Barrier(2)
            outcomes = []
            outcomes_lock = threading.Lock()

            def run_decision(request_id, action, reason=""):
                connection = sqlite3.connect(path, timeout=10)
                connection.row_factory = sqlite3.Row
                try:
                    barrier.wait(timeout=5)
                    result = decide_permission(
                        connection, request_id=request_id, action=action,
                        confirm=True, reason=reason, actor="race-operator",
                    )
                    outcome = (action, "success", result["status"])
                    connection.commit()
                except Exception as exc:  # one competing decision must fail closed
                    outcome = (action, "rejected", type(exc).__name__)
                    connection.rollback()
                finally:
                    connection.close()
                with outcomes_lock:
                    outcomes.append(outcome)

            threads = [
                threading.Thread(target=run_decision, args=(eval_req["id"], "approve")),
                threading.Thread(target=run_decision, args=(test_req["id"], "revoke", "end test")),
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=15)
            self.assertTrue(all(not thread.is_alive() for thread in threads))
            self.assertEqual(len(outcomes), 2)
            self.assertEqual(sum(item[1] == "success" for item in outcomes), 1)

            check = sqlite3.connect(path, timeout=10)
            check.row_factory = sqlite3.Row
            self.assertFalse(
                has_permission(check, **self.ident, scope="EVALUATION")
                and not has_permission(check, **self.ident, scope="TEST_ONLY")
            )
            check.close()

    def test_decision_is_compare_and_set_and_cannot_be_repeated(self):
        req = self.request()
        approved = self.approve(req)
        self.assertEqual(approved["status"], "approved")
        with self.assertRaisesRegex(ValueError, "wyłącznie oczekujące"):
            self.decide(
                request_id=req["id"], action="reject", confirm=True,
                reason="zmiana decyzji",
            )
        self.assertEqual(
            self.db.execute(
                "SELECT COUNT(*) FROM model_permission_events WHERE request_id=?",
                (req["id"],),
            ).fetchone()[0],
            2,
        )

if __name__ == "__main__":
    unittest.main()
