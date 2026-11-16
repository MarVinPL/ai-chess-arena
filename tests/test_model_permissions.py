import hashlib
import sqlite3
import tempfile
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

if __name__ == "__main__":
    unittest.main()
