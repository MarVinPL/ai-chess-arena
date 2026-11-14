import sqlite3
import unittest

from model_permissions import (
    ensure_schema, request_permission, decide_permission, list_permissions, has_permission,
)

class ModelPermissionTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        ensure_schema(self.db)
        self.ident = dict(model_id=1, repo_id="org/model", revision="a" * 40)

    def tearDown(self):
        self.db.close()

    def request(self, scope="TEST_ONLY"):
        return request_permission(self.db, **self.ident, scope=scope,
                                  confirm=True, reason="kontrolowany test")

    def approve(self, request):
        return decide_permission(self.db, request_id=request["id"],
                                  action="approve", confirm=True)

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

    def test_evaluation_requires_separate_test_only_approval(self):
        with self.assertRaisesRegex(ValueError, "TEST_ONLY"):
            self.request("EVALUATION")
        test_req = self.request()
        self.approve(test_req)
        eval_req = self.request("EVALUATION")
        self.assertFalse(has_permission(self.db, **self.ident, scope="EVALUATION"))
        self.approve(eval_req)
        self.assertTrue(has_permission(self.db, **self.ident, scope="EVALUATION"))

    def test_tournament_is_never_granted_by_operational_permission_registry(self):
        with self.assertRaisesRegex(ValueError, "allowlist"):
            self.request("TOURNAMENT")

    def test_revoke_test_only_requires_evaluation_to_be_revoked_first(self):
        test_req = self.request()
        self.approve(test_req)
        eval_req = self.request("EVALUATION")
        self.approve(eval_req)
        with self.assertRaisesRegex(ValueError, "EVALUATION"):
            decide_permission(self.db, request_id=test_req["id"], action="revoke",
                              confirm=True, reason="wycofanie")
        decide_permission(self.db, request_id=eval_req["id"], action="revoke",
                          confirm=True, reason="koniec ewaluacji")
        decide_permission(self.db, request_id=test_req["id"], action="revoke",
                          confirm=True, reason="koniec testów")
        self.assertFalse(has_permission(self.db, **self.ident, scope="TEST_ONLY"))

    def test_reject_requires_reason_and_preserves_history(self):
        req = self.request()
        with self.assertRaisesRegex(ValueError, "uzasadnienie"):
            decide_permission(self.db, request_id=req["id"], action="reject",
                              confirm=True, reason=" ")
        decide_permission(self.db, request_id=req["id"], action="reject",
                          confirm=True, reason="brak dowodów")
        self.assertEqual([x["event"] for x in self.db.execute(
            "SELECT event FROM model_permission_events ORDER BY id")],
            ["requested", "rejected"])
        self.assertEqual(list_permissions(self.db)[0]["status"], "rejected")

    def test_missing_confirmation_and_invalid_identity_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "potwierdzenie"):
            request_permission(self.db, **self.ident, scope="TEST_ONLY",
                               confirm=False, reason="kontrola")
        with self.assertRaisesRegex(ValueError, "SHA"):
            request_permission(self.db, **{**self.ident, "revision": "latest"},
                               scope="TEST_ONLY", confirm=True, reason="kontrola")

if __name__ == "__main__":
    unittest.main()
