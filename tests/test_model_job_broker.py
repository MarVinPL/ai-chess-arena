import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path

import model_permissions
from model_job_broker import (
    WORKER_EXECUTABLE, claim_job_for_start, create_job, finish_job,
    get_job, mark_job_running, record_evaluation_metric,
    request_job_cancel, confirm_job_stopped, reconcile_orphaned_job,
)


class ModelJobBrokerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "managed-models"
        self.root.mkdir()
        self.artifact = self.root / "candidate.gguf"
        self.artifact.write_bytes(b"test-fixture-only")
        self.artifact_sha256 = hashlib.sha256(self.artifact.read_bytes()).hexdigest()
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        model_permissions.ensure_schema(self.db)
        self.ident = dict(model_id=1, repo_id="org/model", revision="a" * 40,
                          artifact_sha256=self.artifact_sha256)
        self._grant("TEST_ONLY")

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def _grant(self, scope):
        req = model_permissions.request_permission(
            self.db, **self.ident, scope=scope, confirm=True,
            reason="kontrolowany test")
        model_permissions.decide_permission(
            self.db, request_id=req["id"], action="approve",
            confirm=True)

    def _create(self, scope="TEST_ONLY", **overrides):
        args = dict(
            model_id=self.ident["model_id"], repo_id=self.ident["repo_id"],
            revision=self.ident["revision"], scope=scope, artifact_path=str(self.artifact),
            expected_sha256=self.artifact_sha256,
            confirm_operation=True, actor="test-owner",
            model_root=self.root, capacity_check=lambda: True,
        )
        args.update(overrides)
        return create_job(self.db, **args)

    def test_no_active_scope_fails_closed(self):
        model_permissions.decide_permission(
            self.db, request_id=1, action="revoke", confirm=True,
            reason="test zakończony")
        with self.assertRaises(PermissionError):
            self._create()

    def test_operation_consent_hash_and_capacity_are_required(self):
        with self.assertRaisesRegex(ValueError, "zgoda"):
            self._create(confirm_operation=False)
        with self.assertRaises(PermissionError):
            self._create(expected_sha256="0" * 64)
        with self.assertRaisesRegex(RuntimeError, "zasobów"):
            self._create(capacity_check=lambda: False)

    def test_claim_consumes_consent_and_returns_fixed_shell_free_plan(self):
        job = self._create()
        plan = claim_job_for_start(
            self.db, job_id=job["job_id"], actor="test-broker", capacity_check=lambda: True,
            model_root=self.root)
        self.assertEqual(plan["status"], "starting")
        self.assertEqual(plan["worker"], WORKER_EXECUTABLE)
        self.assertFalse(plan["side_effects_performed"])
        self.assertIn("--", plan["argv"])
        self.assertEqual(plan["argv"][-9], WORKER_EXECUTABLE)
        self.assertFalse(any(x in {"sh", "bash", "-c"} for x in plan["argv"]))
        with self.assertRaisesRegex(ValueError, "zużyta"):
            claim_job_for_start(
                self.db, job_id=job["job_id"], actor="test-broker", capacity_check=lambda: True,
                model_root=self.root)
        self.assertEqual(get_job(self.db, job["job_id"])["consent_consumed"], 1)

    def test_revocation_after_queue_blocks_claim(self):
        job = self._create()
        req = self.db.execute(
            "SELECT id FROM model_permission_requests WHERE scope='TEST_ONLY' "
            "AND status='approved' ORDER BY id DESC LIMIT 1").fetchone()
        model_permissions.decide_permission(
            self.db, request_id=req["id"], action="revoke", confirm=True,
            reason="cofnięcie zgody")
        with self.assertRaises(PermissionError):
            claim_job_for_start(
                self.db, job_id=job["job_id"], actor="test-broker", capacity_check=lambda: True,
                model_root=self.root)

    def test_artifact_change_after_queue_blocks_claim(self):
        job = self._create()
        self.artifact.write_bytes(b"changed-after-approval")
        with self.assertRaisesRegex(ValueError, "zmienił się"):
            claim_job_for_start(
                self.db, job_id=job["job_id"], actor="test-broker", capacity_check=lambda: True,
                model_root=self.root)

    def test_evaluation_metrics_are_separate_and_scope_gated(self):
        technical = self._create()
        with self.assertRaisesRegex(ValueError, "EVALUATION"):
            record_evaluation_metric(
                self.db, job_id=technical["job_id"], metric_name="latency_ms",
                metric_value=12.5, actor="test-worker")
        self._grant("EVALUATION")
        evaluation = self._create(scope="EVALUATION")
        plan = claim_job_for_start(
            self.db, job_id=evaluation["job_id"], actor="test-broker", capacity_check=lambda: True,
            model_root=self.root)
        mark_job_running(
            self.db, job_id=evaluation["job_id"],
            systemd_unit=plan["systemd_unit"], actor="test-broker")
        record_evaluation_metric(
            self.db, job_id=evaluation["job_id"], metric_name="latency_ms",
            metric_value=12.5, actor="test-worker")
        finish_job(self.db, job_id=evaluation["job_id"], success=True,
                   actor="test-broker", result={"exit_code": 0})
        saved = get_job(self.db, evaluation["job_id"])
        self.assertEqual(saved["status"], "succeeded")
        self.assertEqual(saved["metrics"][0]["metric_name"], "latency_ms")
        self.assertEqual(saved["metrics"][0]["metric_value"], 12.5)

    def test_job_lifecycle_rejects_wrong_unit_and_invalid_transition(self):
        job = self._create()
        plan = claim_job_for_start(
            self.db, job_id=job["job_id"], actor="test-broker", capacity_check=lambda: True,
            model_root=self.root)
        with self.assertRaisesRegex(ValueError, "jednostka"):
            mark_job_running(self.db, job_id=job["job_id"],
                             systemd_unit="untrusted.service", actor="test-broker")
        mark_job_running(self.db, job_id=job["job_id"],
                         systemd_unit=plan["systemd_unit"], actor="test-broker")
        finish_job(self.db, job_id=job["job_id"], success=False,
                   actor="test-broker", result={"reason": "fixture"})
        with self.assertRaisesRegex(ValueError, "zakończone"):
            finish_job(self.db, job_id=job["job_id"], success=True,
                       actor="test-broker")


if __name__ == "__main__":
    unittest.main()
