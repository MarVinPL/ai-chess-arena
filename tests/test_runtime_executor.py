import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import hf_candidate_downloads as downloads
import hf_candidate_runtime as preflight
import league
import model_permissions
import runtime_executor as executor


class RuntimeExecutorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = self.root / 'arena.sqlite3'
        self.quarantine = self.root / 'quarantine'
        self.quarantine.mkdir()
        self.artifact = self.quarantine / 'model.gguf.quarantine'
        self.artifact.write_bytes(b'fake-gguf-for-unit-tests-only')
        self.sha = hashlib.sha256(self.artifact.read_bytes()).hexdigest()
        self.revision = 'a' * 40
        self.catalog_id = 'hf:org/model'
        self.artifact_key = f'{self.catalog_id}|{self.revision}|model.gguf'
        self.model_id = 123
        self.patches = [
            patch.object(league, 'DB', self.db),
            patch.object(downloads, 'DB_PATH', self.db),
            patch.object(downloads, 'STAGING_DIR', self.quarantine),
            patch.object(downloads, 'APPROVED_ARTIFACTS', {(self.catalog_id, self.revision, 'model.gguf')}),
            patch.object(downloads.audit, 'load_catalog', return_value=[{'catalog_id': self.catalog_id, 'repo_id': 'org/model'}]),
            patch.object(preflight, 'refresh_preflight', return_value={'items': []}),
            patch.object(preflight, 'authorize_runtime_handoff', return_value={
                'ok': True, 'path': str(self.artifact), 'sha256': self.sha,
                'size_bytes': self.artifact.stat().st_size,
            }),
        ]
        for item in self.patches:
            item.start()
        downloads.init_db(self.db)
        stat = self.artifact.stat()
        with downloads._db() as connection:
            connection.execute('INSERT INTO hf_download_verified_artifacts VALUES(?,?,?,?,?,?)',
                (self.artifact_key, str(self.artifact), stat.st_size, self.sha,
                 '2026-11-16T00:00:00Z', stat.st_mtime_ns))
        with league.connect() as connection:
            model_permissions.ensure_schema(connection)
            request = model_permissions.request_permission(
                connection, model_id=self.model_id, repo_id='org/model',
                revision=self.revision, artifact_sha256=self.sha,
                scope='TEST_ONLY', confirm=True, reason='Test executor unit test', actor='test-operator')
            self.permission_id = request['id']
            model_permissions.decide_permission(connection, request_id=self.permission_id,
                action='approve', confirm=True, actor='test-operator')

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.tmp.cleanup()

    def enqueue(self):
        return executor.enqueue(model_id=self.model_id, repo_id='org/model',
            revision=self.revision, artifact_sha256=self.sha,
            artifact_key=self.artifact_key, permission_request_id=self.permission_id,
            actor='test-operator')

    def test_enqueue_requires_active_exact_test_only_grant(self):
        queued = self.enqueue()
        self.assertEqual(queued['status'], 'queued')
        self.assertFalse(queued['runtime_started'])
        with league.connect() as connection:
            model_permissions.decide_permission(connection, request_id=self.permission_id,
                action='revoke', confirm=True, reason='Test revoke', actor='test-operator')
        with self.assertRaisesRegex(ValueError, 'aktywna, zatwierdzona zgoda TEST_ONLY'):
            self.enqueue()

    def test_enqueue_rejects_mismatched_artifact_hash(self):
        with self.assertRaises(ValueError):
            executor.enqueue(model_id=self.model_id, repo_id='org/model',
                revision=self.revision, artifact_sha256='b' * 64,
                artifact_key=self.artifact_key, permission_request_id=self.permission_id,
                actor='test-operator')

    def test_no_default_runner_means_no_execution(self):
        queued = self.enqueue()
        with self.assertRaisesRegex(RuntimeError, 'Brak jawnie skonfigurowanego'):
            executor.execute_job(queued['job_id'], lock_path=self.root / 'lock')
        self.assertEqual(executor.snapshot()['jobs'][0]['status'], 'queued')

    def test_one_job_runs_with_explicit_runner_and_records_result(self):
        queued = self.enqueue()
        seen = {}
        def runner(payload, timeout, cancelled):
            seen.update(payload)
            self.assertEqual(timeout, executor.DEFAULT_TIMEOUT_SECONDS)
            self.assertFalse(cancelled())
            return {'exit_code': 0, 'summary': 'fake runner only'}
        result = executor.execute_job(queued['job_id'], runner=runner, lock_path=self.root / 'lock')
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(seen['sha256'], self.sha)
        self.assertEqual(seen['path'], str(self.artifact))
        self.assertEqual(executor.snapshot()['jobs'][0]['status'], 'completed')

    def test_cancel_queued_job_without_starting_runtime(self):
        queued = self.enqueue()
        result = executor.cancel(queued['job_id'])
        self.assertEqual(result['status'], 'cancelled')
        self.assertEqual(executor.snapshot()['jobs'][0]['status'], 'cancelled')

    def test_global_lock_rejects_parallel_execution(self):
        queued = self.enqueue()
        lock = self.root / 'lock'
        lock.parent.mkdir(exist_ok=True)
        with lock.open('a+') as held:
            import fcntl
            fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(RuntimeError, 'globalna blokada'):
                executor.execute_job(queued['job_id'], runner=lambda *_: {}, lock_path=lock)
        self.assertEqual(executor.snapshot()['jobs'][0]['status'], 'queued')

    def test_permission_revoked_during_runner_marks_job_cancelled(self):
        queued = self.enqueue()
        def runner(payload, timeout, cancelled):
            with league.connect() as connection:
                model_permissions.decide_permission(connection, request_id=self.permission_id,
                    action='revoke', confirm=True, reason='Revoked during test', actor='test-operator')
            self.assertTrue(cancelled())
            return {'exit_code': 0}
        result = executor.execute_job(queued['job_id'], runner=runner, lock_path=self.root / 'lock')
        self.assertEqual(result['status'], 'cancelled')


if __name__ == '__main__':
    unittest.main()
