import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import hf_candidate_downloads as downloads
import hf_candidate_runtime as preflight
import league
import model_permissions
import runtime_executor as executor
from runtime_api_worker import RuntimeApiWorker
import server


class RuntimeExecutorTests(unittest.TestCase):
    def setUp(self):
        self.old_runtime_opt_in = os.environ.get('ARENA_RUNTIME_EXECUTION_ENABLED')
        os.environ['ARENA_RUNTIME_EXECUTION_ENABLED'] = '1'
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
        if self.old_runtime_opt_in is None:
            os.environ.pop('ARENA_RUNTIME_EXECUTION_ENABLED', None)
        else:
            os.environ['ARENA_RUNTIME_EXECUTION_ENABLED'] = self.old_runtime_opt_in

    def enqueue(self):
        return executor.enqueue(model_id=self.model_id, repo_id='org/model',
            revision=self.revision, artifact_sha256=self.sha,
            artifact_key=self.artifact_key, permission_request_id=self.permission_id,
            actor='test-operator')

    def approve_for_fake_execution(self, job_id):
        batch = executor.create_batch(job_ids=[job_id], actor='test-operator')
        executor.set_execution_enabled(enabled=True, confirm=True,
            actor='test-operator', reason='Unit test fake runner only')
        executor.approve_batch(batch_id=batch['batch_id'], confirm=True,
            actor='test-operator', reason='Unit test fake runner only')
        return batch['batch_id']

    def test_production_api_dispatcher_executes_only_approved_test_job(self):
        queued = self.enqueue()
        self.approve_for_fake_execution(queued['job_id'])

        class ProductionProfile:
            production_requested = True
            execution_enabled = True

        runner_calls = []
        worker = RuntimeApiWorker(
            deployment=ProductionProfile(),
            runner=lambda payload, timeout, cancelled: runner_calls.append(
                (payload['id'], timeout, callable(cancelled))
            ) or {'ok': True, 'runtime': 'fake-test-runner'},
        )
        result = worker.run_once()
        self.assertEqual(result, {
            'status': 'dispatched', 'job_id': queued['job_id'],
            'result_status': 'completed',
        })
        self.assertEqual(runner_calls, [(queued['job_id'], executor.DEFAULT_TIMEOUT_SECONDS, True)])
        job = executor.snapshot()['jobs'][0]
        self.assertEqual(job['status'], 'completed')
        with league.connect() as connection:
            persisted = connection.execute(
                'SELECT result_json FROM runtime_test_jobs WHERE id=?',
                (queued['job_id'],),
            ).fetchone()
        self.assertIsNotNone(persisted['result_json'])

    def test_batch_creation_does_not_enable_runtime_or_start_model(self):
        queued = self.enqueue()
        batch = executor.create_batch(job_ids=[queued['job_id']], actor='test-operator')
        state = executor.snapshot()
        self.assertEqual(batch['status'], 'pending_approval')
        self.assertFalse(state['execution_enabled'])
        self.assertFalse(state['runtime_started'])
        self.assertEqual(state['jobs'][0]['batch_id'], batch['batch_id'])

    def test_batch_cannot_be_approved_before_runtime_is_enabled(self):
        queued = self.enqueue()
        batch = executor.create_batch(job_ids=[queued['job_id']], actor='test-operator')
        with self.assertRaisesRegex(PermissionError, 'niezależnie włączony'):
            executor.approve_batch(batch_id=batch['batch_id'], confirm=True,
                actor='test-operator', reason='Explicit approval test')
        self.assertEqual(executor.snapshot()['batches'][0]['status'], 'pending_approval')

    def test_batch_approval_is_required_before_fake_runner_can_start(self):
        queued = self.enqueue()
        batch = executor.create_batch(job_ids=[queued['job_id']], actor='test-operator')
        executor.set_execution_enabled(enabled=True, confirm=True,
            actor='test-operator', reason='Enable for test only')
        invoked = []
        with self.assertRaisesRegex(PermissionError, 'zatwierdzenia tej partii'):
            executor.execute_job(queued['job_id'],
                runner=lambda *_: invoked.append(True) or {'exit_code': 0},
                lock_path=self.root / 'lock')
        self.assertEqual(invoked, [])
        self.assertEqual(executor.snapshot()['jobs'][0]['status'], 'failed')

    def test_expired_batch_is_marked_expired_and_queued_jobs_cancelled(self):
        queued = self.enqueue()
        batch = executor.create_batch(job_ids=[queued['job_id']], actor='test-operator')
        executor.set_execution_enabled(enabled=True, confirm=True,
            actor='test-operator', reason='Enable for expiry test')
        executor.approve_batch(batch_id=batch['batch_id'], confirm=True,
            actor='test-operator', reason='Approval expiry test', approval_seconds=30)
        with patch.object(executor, '_now', return_value='2999-01-01T00:00:00Z'):
            state = executor.snapshot()
        self.assertEqual(state['batches'][0]['status'], 'expired')
        self.assertEqual(state['jobs'][0]['status'], 'cancelled')

    def test_disabling_runtime_during_runner_signals_cancellation(self):
        queued = self.enqueue()
        self.approve_for_fake_execution(queued['job_id'])
        def runner(payload, timeout, cancelled):
            executor.set_execution_enabled(enabled=False, confirm=True,
                actor='test-operator', reason='Emergency runtime disable')
            self.assertTrue(cancelled())
            return {'exit_code': 0}
        result = executor.execute_job(queued['job_id'], runner=runner,
            lock_path=self.root / 'lock')
        self.assertEqual(result['status'], 'cancelled')
        self.assertEqual(executor.snapshot()['batches'][0]['status'], 'revoked')

    def test_disabling_runtime_revokes_batches_and_cancels_queued_jobs(self):
        queued = self.enqueue()
        batch = executor.create_batch(job_ids=[queued['job_id']], actor='test-operator')
        executor.set_execution_enabled(enabled=True, confirm=True,
            actor='test-operator', reason='Enable for test only')
        executor.approve_batch(batch_id=batch['batch_id'], confirm=True,
            actor='test-operator', reason='Approve for test only')
        executor.set_execution_enabled(enabled=False, confirm=True,
            actor='test-operator', reason='Disable after test')
        state = executor.snapshot()
        self.assertEqual(state['batches'][0]['status'], 'revoked')
        self.assertEqual(state['jobs'][0]['status'], 'cancelled')
        self.assertFalse(state['execution_enabled'])

    def test_runtime_cannot_be_enabled_without_environment_opt_in(self):
        with patch.dict(os.environ, {"ARENA_RUNTIME_EXECUTION_ENABLED": ""}):
            with self.assertRaisesRegex(PermissionError, 'ARENA_RUNTIME_EXECUTION_ENABLED'):
                executor.set_execution_enabled(enabled=True, confirm=True,
                    actor='test-operator', reason='No environment opt-in')
        self.assertFalse(executor.snapshot()['execution_enabled'])

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

    def test_revoked_permission_before_execution_marks_queued_job_failed(self):
        queued = self.enqueue()
        self.approve_for_fake_execution(queued['job_id'])
        with league.connect() as connection:
            model_permissions.decide_permission(
                connection, request_id=self.permission_id, action='revoke',
                confirm=True, reason='Revoked before execution', actor='test-operator')
        with self.assertRaisesRegex(PermissionError, 'wycofana lub jest nieaktywna'):
            executor.execute_job(
                queued['job_id'], runner=lambda *_: {'exit_code': 0},
                lock_path=self.root / 'lock')
        job = executor.snapshot()['jobs'][0]
        self.assertEqual(job['status'], 'failed')
        self.assertIn('wycofana', job['error'])

    def test_recovery_marks_running_job_failed_and_only_terminates_verified_group(self):
        queued = self.enqueue()
        batch_id = self.approve_for_fake_execution(queued['job_id'])
        with league.connect() as connection:
            connection.execute("UPDATE runtime_test_jobs SET status='running',process_pid=4242,process_start_ticks=123 WHERE id=?",
                               (queued['job_id'],))
            connection.execute("UPDATE runtime_test_batches SET status='running' WHERE id=?", (batch_id,))
        with patch.object(executor, '_terminate_verified_orphan', return_value=True) as terminate:
            result = executor.recover_orphaned_jobs()
        terminate.assert_called_once_with(4242, 123)
        self.assertEqual(result['count'], 1)
        state = executor.snapshot()
        self.assertEqual(state['jobs'][0]['status'], 'failed')
        self.assertEqual(state['batches'][0]['status'], 'failed')

    def test_orphan_pid_identity_mismatch_is_never_signalled(self):
        from runtime_executor import _terminate_verified_orphan
        identity = {'start_ticks': 999, 'pgrp': 4242, 'session': 4242,
                    'cmdline': '/home/debian/ai-agent/bin/llamafile',
                    'exe': '/home/debian/ai-agent/bin/llamafile'}
        with patch.object(executor, '_process_identity', return_value=identity), \
             patch.object(executor.os, 'killpg') as killpg:
            self.assertFalse(_terminate_verified_orphan(4242, 123))
        killpg.assert_not_called()

    def test_no_default_runner_means_no_execution(self):
        queued = self.enqueue()
        with self.assertRaisesRegex(RuntimeError, 'Brak jawnie skonfigurowanego'):
            executor.execute_job(queued['job_id'], lock_path=self.root / 'lock')
        self.assertEqual(executor.snapshot()['jobs'][0]['status'], 'queued')

    def test_one_job_runs_with_explicit_runner_and_records_result(self):
        queued = self.enqueue()
        self.approve_for_fake_execution(queued['job_id'])
        seen = {}
        def runner(payload, timeout, cancelled):
            seen.update(payload)
            self.assertEqual(timeout, executor.DEFAULT_TIMEOUT_SECONDS)
            self.assertFalse(cancelled())
            return {'exit_code': 0, 'summary': 'fake runner only'}
        def runner(payload, timeout, cancelled):
            seen.update(payload)
            self.assertEqual(timeout, executor.DEFAULT_TIMEOUT_SECONDS)
            self.assertFalse(cancelled())
            payload['register_process'](4321, 4321, 987654)
            return {'exit_code': 0, 'summary': 'fake runner only'}
        result = executor.execute_job(queued['job_id'], runner=runner, lock_path=self.root / 'lock')
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(seen['sha256'], self.sha)
        self.assertEqual(executor.snapshot()['jobs'][0]['process_pid'], 4321)
        self.assertEqual(seen['path'], str(self.artifact))
        job = executor.snapshot()['jobs'][0]
        self.assertEqual(job['status'], 'completed')
        with league.connect() as connection:
            stored_result = connection.execute(
                'SELECT result_json FROM runtime_test_jobs WHERE id=?',
                (queued['job_id'],)).fetchone()['result_json']
        self.assertEqual(__import__('json').loads(stored_result),
                         {'exit_code': 0, 'summary': 'fake runner only'})

    def test_result_commit_rejects_cancellation_that_won_the_race(self):
        queued = self.enqueue()
        batch_id = self.approve_for_fake_execution(queued['job_id'])
        with league.connect() as connection:
            connection.execute("UPDATE runtime_test_jobs SET status='running',started_at=? WHERE id=?",
                               (executor._now(), queued['job_id']))
            connection.execute("UPDATE runtime_test_batches SET status='running' WHERE id=?",
                               (batch_id,))
            connection.execute("UPDATE runtime_test_jobs SET cancel_requested=1 WHERE id=?",
                               (queued['job_id'],))
        committed = executor._persist_authorized_result(
            job_id=queued['job_id'], batch_id=batch_id,
            permission_request_id=self.permission_id, result={'ok': True})
        self.assertFalse(committed)
        job = executor.snapshot()['jobs'][0]
        self.assertEqual(job['status'], 'running')
        with league.connect() as connection:
            stored_result = connection.execute(
                'SELECT result_json FROM runtime_test_jobs WHERE id=?',
                (queued['job_id'],)).fetchone()['result_json']
        self.assertIsNone(stored_result)

    def test_result_commit_rejects_revoked_test_only_grant(self):
        queued = self.enqueue()
        batch_id = self.approve_for_fake_execution(queued['job_id'])
        with league.connect() as connection:
            connection.execute("UPDATE runtime_test_jobs SET status='running',started_at=? WHERE id=?",
                               (executor._now(), queued['job_id']))
            connection.execute("UPDATE runtime_test_batches SET status='running' WHERE id=?",
                               (batch_id,))
            model_permissions.decide_permission(
                connection, request_id=self.permission_id, action='revoke',
                confirm=True, reason='Revoke before terminal commit', actor='test-operator')
        committed = executor._persist_authorized_result(
            job_id=queued['job_id'], batch_id=batch_id,
            permission_request_id=self.permission_id, result={'ok': True})
        self.assertFalse(committed)
        job = executor.snapshot()['jobs'][0]
        self.assertEqual(job['status'], 'running')
        with league.connect() as connection:
            stored_result = connection.execute(
                'SELECT result_json FROM runtime_test_jobs WHERE id=?',
                (queued['job_id'],)).fetchone()['result_json']
        self.assertIsNone(stored_result)

    def test_supervised_runner_stops_exact_unit_when_job_is_cancelled(self):
        from runtime_supervisor_client import RuntimeSupervisorClientRunner
        queued = self.enqueue()
        self.approve_for_fake_execution(queued['job_id'])
        actions = []
        def supervisor_call(_socket, action, job_id, *, timeout_seconds):
            actions.append((action, job_id))
            if action == 'start':
                executor.cancel(job_id)
                return {'job_id': job_id, 'unit_name':
                        f'ai-chess-arena-runtime-{job_id}.service',
                        'lease_state': 'active', 'unit_state': 'start_requested'}
            if action == 'stop':
                return {'job_id': job_id, 'unit_name':
                        f'ai-chess-arena-runtime-{job_id}.service',
                        'lease_state': 'stopped'}
            raise AssertionError(action)
        runner = RuntimeSupervisorClientRunner(
            supervisor_call=supervisor_call,
            bridge_call=lambda *_args, **_kwargs: (200, b'healthy'),
            sleep=lambda _seconds: None)
        result = executor.execute_job(
            queued['job_id'], runner=runner, lock_path=self.root / 'lock')
        self.assertEqual(result['status'], 'cancelled')
        self.assertEqual(actions, [('start', queued['job_id']), ('stop', queued['job_id'])])
        job = executor.snapshot()['jobs'][0]
        self.assertEqual(job['status'], 'cancelled')
        with league.connect() as connection:
            stored_result = connection.execute(
                'SELECT result_json FROM runtime_test_jobs WHERE id=?',
                (queued['job_id'],)).fetchone()['result_json']
        self.assertIsNone(stored_result)

    def test_supervised_runner_stops_exact_unit_when_batch_is_revoked(self):
        from runtime_supervisor_client import RuntimeSupervisorClientRunner
        queued = self.enqueue()
        batch_id = self.approve_for_fake_execution(queued['job_id'])
        actions = []
        def supervisor_call(_socket, action, job_id, *, timeout_seconds):
            actions.append((action, job_id))
            if action == 'start':
                executor.revoke_batch(
                    batch_id=batch_id, actor='test-operator',
                    reason='Revoked during supervised runner test')
                return {'job_id': job_id, 'unit_name':
                        f'ai-chess-arena-runtime-{job_id}.service',
                        'lease_state': 'active', 'unit_state': 'start_requested'}
            if action == 'stop':
                return {'job_id': job_id, 'unit_name':
                        f'ai-chess-arena-runtime-{job_id}.service',
                        'lease_state': 'stopped'}
            raise AssertionError(action)
        runner = RuntimeSupervisorClientRunner(
            supervisor_call=supervisor_call,
            bridge_call=lambda *_args, **_kwargs: (200, b'healthy'),
            sleep=lambda _seconds: None)
        result = executor.execute_job(
            queued['job_id'], runner=runner, lock_path=self.root / 'lock')
        self.assertEqual(result['status'], 'cancelled')
        self.assertEqual(actions, [('start', queued['job_id']), ('stop', queued['job_id'])])
        self.assertEqual(executor.snapshot()['batches'][0]['status'], 'revoked')

    def test_cancel_serializes_with_a_completion_that_commits_first(self):
        import threading
        from concurrent.futures import ThreadPoolExecutor
        queued = self.enqueue()
        batch_id = self.approve_for_fake_execution(queued['job_id'])
        with league.connect() as connection:
            connection.execute("UPDATE runtime_test_jobs SET status='running' WHERE id=?",
                               (queued['job_id'],))
            connection.execute("UPDATE runtime_test_batches SET status='running' WHERE id=?",
                               (batch_id,))
        locked = threading.Event()
        release = threading.Event()
        def complete_first():
            with league.connect() as connection:
                connection.execute('BEGIN IMMEDIATE')
                locked.set()
                self.assertTrue(release.wait(3))
                connection.execute("UPDATE runtime_test_jobs SET status='completed' WHERE id=?",
                                   (queued['job_id'],))
                connection.execute("UPDATE runtime_test_batches SET status='completed' WHERE id=?",
                                   (batch_id,))
        with ThreadPoolExecutor(max_workers=2) as pool:
            completion = pool.submit(complete_first)
            self.assertTrue(locked.wait(2))
            cancellation = pool.submit(executor.cancel, queued['job_id'])
            release.set()
            completion.result(timeout=4)
            self.assertEqual(cancellation.result(timeout=4)['status'], 'completed')
        job = executor.snapshot()['jobs'][0]
        self.assertEqual(job['status'], 'completed')

    def test_batch_revoke_serializes_with_completion_that_commits_first(self):
        import threading
        from concurrent.futures import ThreadPoolExecutor
        queued = self.enqueue()
        batch_id = self.approve_for_fake_execution(queued['job_id'])
        with league.connect() as connection:
            connection.execute("UPDATE runtime_test_jobs SET status='running' WHERE id=?",
                               (queued['job_id'],))
            connection.execute("UPDATE runtime_test_batches SET status='running' WHERE id=?",
                               (batch_id,))
        locked = threading.Event()
        release = threading.Event()
        def complete_first():
            with league.connect() as connection:
                connection.execute('BEGIN IMMEDIATE')
                locked.set()
                self.assertTrue(release.wait(3))
                connection.execute("UPDATE runtime_test_jobs SET status='completed' WHERE id=?",
                                   (queued['job_id'],))
                connection.execute("UPDATE runtime_test_batches SET status='completed' WHERE id=?",
                                   (batch_id,))
        with ThreadPoolExecutor(max_workers=2) as pool:
            completion = pool.submit(complete_first)
            self.assertTrue(locked.wait(2))
            revocation = pool.submit(
                executor.revoke_batch, batch_id=batch_id, actor='test-operator',
                reason='Race with completed batch')
            release.set()
            completion.result(timeout=4)
            with self.assertRaisesRegex(ValueError, 'Nie można wycofać'):
                revocation.result(timeout=4)
        state = executor.snapshot()
        self.assertEqual(state['jobs'][0]['status'], 'completed')
        self.assertEqual(state['batches'][0]['status'], 'completed')

    def test_cancel_queued_job_without_starting_runtime(self):
        queued = self.enqueue()
        result = executor.cancel(queued['job_id'])
        self.assertEqual(result['status'], 'cancelled')
        self.assertEqual(executor.snapshot()['jobs'][0]['status'], 'cancelled')

    def test_cancel_reports_completed_when_terminal_commit_already_won(self):
        queued = self.enqueue()
        batch_id = self.approve_for_fake_execution(queued['job_id'])
        with league.connect() as connection:
            connection.execute(
                "UPDATE runtime_test_jobs SET status='completed',finished_at=? WHERE id=?",
                (executor._now(), queued['job_id']),
            )
        result = executor.cancel(queued['job_id'])
        self.assertEqual(result, {'job_id': queued['job_id'], 'status': 'completed'})
        self.assertEqual(executor.snapshot()['jobs'][0]['status'], 'completed')

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
        self.approve_for_fake_execution(queued['job_id'])
        def runner(payload, timeout, cancelled):
            with league.connect() as connection:
                model_permissions.decide_permission(connection, request_id=self.permission_id,
                    action='revoke', confirm=True, reason='Revoked during test', actor='test-operator')
            self.assertTrue(cancelled())
            return {'exit_code': 0}
        result = executor.execute_job(queued['job_id'], runner=runner, lock_path=self.root / 'lock')
        self.assertEqual(result['status'], 'cancelled')



class RuntimeExecutorMigrationTests(unittest.TestCase):
    def test_existing_queue_schema_gains_created_by_column(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "legacy.sqlite3"
            connection = __import__("sqlite3").connect(db_path)
            connection.execute("""CREATE TABLE runtime_test_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                model_id INTEGER NOT NULL, repo_id TEXT NOT NULL,
                revision TEXT NOT NULL, artifact_key TEXT NOT NULL,
                artifact_sha256 TEXT NOT NULL, permission_request_id INTEGER NOT NULL,
                status TEXT NOT NULL, timeout_seconds INTEGER NOT NULL,
                queued_at TEXT NOT NULL, started_at TEXT, finished_at TEXT,
                cancel_requested INTEGER NOT NULL DEFAULT 0,
                result_json TEXT, error TEXT
            )""")
            executor._init(connection)
            columns = {row[1] for row in connection.execute(
                "PRAGMA table_info(runtime_test_jobs)")}
            self.assertIn("created_by", columns)
            self.assertIn("batch_id", columns)
            self.assertIn("process_pid", columns)
            self.assertIn("process_start_ticks", columns)
            connection.close()


class RuntimeExecutorHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import json
        import os
        import threading
        from http.server import ThreadingHTTPServer
        cls.json = json
        cls.old_env = {key: os.environ.get(key) for key in (
            "ARENA_AUTH_USERNAME", "ARENA_AUTH_PASSWORD_HASH",
            "ARENA_AUTH_SESSION_SECRET", "ARENA_AUTH_ORIGIN")}
        os.environ.update({
            "ARENA_AUTH_USERNAME": "arena",
            "ARENA_AUTH_PASSWORD_HASH": server.arena_auth.hash_password(
                "runtime-http-test-password-long-enough"),
            "ARENA_AUTH_SESSION_SECRET": server.arena_auth._b64e(
                b"runtime-http-test-session-secret-123456789"),
            "ARENA_AUTH_ORIGIN": "https://arena.148-113-182-137.sslip.io",
        })
        cls.origin = os.environ["ARENA_AUTH_ORIGIN"]
        cls.http = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.thread = threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.http.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()
        cls.thread.join(timeout=2)
        import os
        for key, value in cls.old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def setUp(self):
        import sqlite3
        import tempfile
        from unittest.mock import patch
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "http.sqlite3"
        def connect():
            connection = sqlite3.connect(self.db_path, timeout=10)
            connection.row_factory = sqlite3.Row
            return connection
        self.connect_patch = patch.object(server.league, "connect", side_effect=connect)
        self.connect_patch.start()
        token = server.arena_auth.create_session_token("arena")
        self.cookie = f"{server.arena_auth.COOKIE_NAME}={token}"

    def tearDown(self):
        self.connect_patch.stop()
        self.tempdir.cleanup()

    def request(self, path, method="GET", payload=None, authenticated=True,
                origin=None):
        import urllib.error
        import urllib.request
        headers = {}
        body = None
        if payload is not None:
            body = self.json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if authenticated:
            headers["Cookie"] = self.cookie
        if origin is not None:
            headers["Origin"] = origin
        request = urllib.request.Request(
            self.base + path, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, self.json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, self.json.loads(error.read())

    def test_runtime_jobs_get_requires_authenticated_session(self):
        status, body = self.request(
            "/api/hf-candidates/runtime-jobs", authenticated=False)
        self.assertEqual(status, 401)
        self.assertIn("Wymagane logowanie", body["error"])

    def test_runtime_jobs_get_returns_queue_without_starting_runtime(self):
        status, body = self.request("/api/hf-candidates/runtime-jobs")
        self.assertEqual(status, 200)
        self.assertEqual(body["jobs"], [])
        self.assertFalse(body["auto_start"])
        self.assertFalse(body["runtime_started"])

    def test_runtime_enqueue_rejects_untrusted_origin(self):
        status, body = self.request(
            "/api/hf-candidates/runtime-enqueue", method="POST",
            payload={}, origin="https://attacker.example")
        self.assertEqual(status, 403)
        self.assertIn("spoza dozwolonej domeny", body["error"])

    def test_runtime_enqueue_requires_authenticated_session(self):
        status, body = self.request(
            "/api/hf-candidates/runtime-enqueue", method="POST",
            payload={}, authenticated=False, origin=self.origin)
        self.assertEqual(status, 401)
        self.assertIn("Wymagane logowanie", body["error"])

    def test_runtime_enqueue_rejects_missing_exact_permission_fields(self):
        status, body = self.request(
            "/api/hf-candidates/runtime-enqueue", method="POST",
            payload={}, origin=self.origin)
        self.assertEqual(status, 422)
        self.assertTrue(body.get("error"))

    def test_runtime_enable_requires_environment_opt_in(self):
        import os
        from unittest.mock import patch
        with patch.dict(os.environ, {"ARENA_RUNTIME_EXECUTION_ENABLED": ""}):
            status, body = self.request(
                "/api/hf-candidates/runtime-enable", method="POST",
                payload={"enabled": True, "confirm": True,
                         "reason": "Explicit operator test"}, origin=self.origin)
        self.assertEqual(status, 422)
        self.assertIn("ARENA_RUNTIME_EXECUTION_ENABLED", body["error"])

    def test_runtime_batch_create_requires_authenticated_session(self):
        status, body = self.request(
            "/api/hf-candidates/runtime-batch-create", method="POST",
            payload={"job_ids": [1]}, authenticated=False, origin=self.origin)
        self.assertEqual(status, 401)
        self.assertIn("Wymagane logowanie", body["error"])

    def test_runtime_batch_create_validates_queued_jobs(self):
        status, body = self.request(
            "/api/hf-candidates/runtime-batch-create", method="POST",
            payload={"job_ids": [999]}, origin=self.origin)
        self.assertEqual(status, 422)
        self.assertTrue(body.get("error"))


class RuntimeSubprocessRunnerTests(unittest.TestCase):
    class Response:
        def __init__(self, status=200, body=b'{}'):
            self.status = status
            self.body = body
        def __enter__(self):
            return self
        def __exit__(self, *_):
            return False
        def read(self):
            return self.body

    class Process:
        pid = 987654
        def poll(self):
            return None

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.quarantine = self.root / 'quarantine'
        self.quarantine.mkdir()
        self.artifact = self.quarantine / 'test.gguf.quarantine'
        self.artifact.write_bytes(b'fake-gguf-runtime-runner-test')
        self.sha = hashlib.sha256(self.artifact.read_bytes()).hexdigest()
        self.patch_staging = patch.object(downloads, 'STAGING_DIR', self.quarantine)
        self.patch_staging.start()

    def tearDown(self):
        self.patch_staging.stop()
        self.tmp.cleanup()

    def payload(self):
        return {'path': str(self.artifact), 'sha256': self.sha,
                'model_id': 1, 'repo_id': 'test/model', 'revision': 'a' * 40}

    def test_rejects_artifact_outside_quarantine_before_process_start(self):
        outside = self.root / 'outside.gguf'
        outside.write_bytes(b'outside')
        calls = []
        runner = __import__('runtime_subprocess').LlamafileSmokeRunner(
            popen=lambda *a, **k: calls.append((a, k)),
            terminate=lambda *_: None, temp_root=self.root)
        payload = self.payload()
        payload['path'] = str(outside)
        with self.assertRaisesRegex(PermissionError, 'wewnątrz kwarantanny'):
            runner(payload, 30, lambda: False)
        self.assertEqual(calls, [])

    def test_rejects_hash_mismatch_before_process_start(self):
        calls = []
        runner = __import__('runtime_subprocess').LlamafileSmokeRunner(
            popen=lambda *a, **k: calls.append((a, k)),
            terminate=lambda *_: None, temp_root=self.root)
        payload = self.payload()
        payload['sha256'] = 'b' * 64
        with self.assertRaisesRegex(PermissionError, 'SHA-256'):
            runner(payload, 30, lambda: False)
        self.assertEqual(calls, [])

    def test_fake_process_smoke_test_is_supervised_and_output_is_cleaned(self):
        import json
        from runtime_subprocess import LlamafileSmokeRunner, WORKER
        calls = []
        process = self.Process()
        def popen(command, **kwargs):
            calls.append((command, kwargs))
            return process
        def urlopen(request, timeout):
            if request.full_url.endswith('/health'):
                return self.Response(200)
            return self.Response(200, json.dumps({
                'choices': [{'message': {'content': 'READY'}}]
            }).encode())
        runner = LlamafileSmokeRunner(
            popen=popen, urlopen=urlopen, monotonic=__import__('time').monotonic,
            sleep=lambda _: None, temp_root=self.root, terminate=lambda p: None, process_identity=lambda pid: (pid, pid, 123456))
        result = runner(self.payload(), 30, lambda: False)
        self.assertTrue(result['ok'])
        self.assertEqual(result['response_excerpt'], 'READY')
        command, kwargs = calls[0]
        self.assertEqual(command[1], str(WORKER))
        self.assertTrue(kwargs['start_new_session'])
        self.assertEqual(kwargs['stdin'], __import__('subprocess').DEVNULL)
        self.assertFalse(Path(kwargs['cwd']).exists())

    def test_cancellation_during_startup_terminates_owned_process(self):
        from runtime_subprocess import LlamafileSmokeRunner
        process = self.Process()
        terminated = []
        def urlopen(_request, timeout):
            raise OSError('not ready yet')
        calls = {'count': 0}
        def cancelled():
            calls['count'] += 1
            return calls['count'] >= 2
        runner = LlamafileSmokeRunner(
            popen=lambda *_a, **_k: process, urlopen=urlopen,
            sleep=lambda _: None, temp_root=self.root,
            terminate=lambda proc: terminated.append(proc.pid), process_identity=lambda pid: (pid, pid, 123456))
        with self.assertRaisesRegex(InterruptedError, 'anulowano'):
            runner(self.payload(), 30, cancelled)
        self.assertEqual(terminated, [process.pid])


class RuntimeProcessGroupTests(unittest.TestCase):
    def test_term_then_kill_escalation_stops_only_owned_test_process_group(self):
        import subprocess
        import sys
        from runtime_subprocess import _stop_process_group
        process = subprocess.Popen(
            [sys.executable, '-c',
             'import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)'],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True)
        try:
            _stop_process_group(process, wait_seconds=0.2)
            self.assertIsNotNone(process.poll())
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=2)


class RuntimeSubprocessTimeoutTests(unittest.TestCase):
    def test_startup_has_hard_wall_clock_timeout(self):
        from runtime_subprocess import LlamafileSmokeRunner
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            quarantine = root / 'quarantine'
            quarantine.mkdir()
            model = quarantine / 'slow.gguf.quarantine'
            model.write_bytes(b'fake-slow-model')
            sha = hashlib.sha256(model.read_bytes()).hexdigest()
            process = RuntimeSubprocessRunnerTests.Process()
            clock = {'now': 0.0}
            terminated = []
            def monotonic():
                return clock['now']
            def sleep(seconds):
                clock['now'] += 0.6
            def urlopen(_request, timeout):
                raise OSError('health not ready')
            with patch.object(downloads, 'STAGING_DIR', quarantine):
                runner = __import__('runtime_subprocess').LlamafileSmokeRunner(
                    popen=lambda *_a, **_k: process, urlopen=urlopen,
                    monotonic=monotonic, sleep=sleep, temp_root=root,
                    terminate=lambda proc: terminated.append(proc.pid), process_identity=lambda pid: (pid, pid, 123456))
                with self.assertRaisesRegex(TimeoutError, 'twardy timeout'):
                    runner({'path': str(model), 'sha256': sha}, 1, lambda: False)
            self.assertEqual(terminated, [process.pid])


class RuntimeInferenceTimeoutTests(unittest.TestCase):
    def test_inference_obeys_same_hard_deadline_and_cleans_temporary_output(self):
        import json
        import threading
        from runtime_subprocess import LlamafileSmokeRunner
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            quarantine = root / 'quarantine'
            quarantine.mkdir()
            model = quarantine / 'slow-inference.gguf.quarantine'
            model.write_bytes(b'fake-inference-model')
            sha = hashlib.sha256(model.read_bytes()).hexdigest()
            process = RuntimeSubprocessRunnerTests.Process()
            clock = {'now': 0.0}
            inference_started = threading.Event()
            release_inference = threading.Event()
            terminated = []
            created_cwd = []
            def monotonic():
                return clock['now']
            def sleep(seconds):
                clock['now'] += 0.6
            def popen(_command, **kwargs):
                created_cwd.append(kwargs['cwd'])
                return process
            def urlopen(request, timeout):
                if request.full_url.endswith('/health'):
                    return RuntimeSubprocessRunnerTests.Response(200)
                inference_started.set()
                release_inference.wait(2)
                return RuntimeSubprocessRunnerTests.Response(200, json.dumps({
                    'choices': [{'message': {'content': 'READY'}}]
                }).encode())
            with patch.object(downloads, 'STAGING_DIR', quarantine):
                runner = LlamafileSmokeRunner(
                    popen=popen, urlopen=urlopen, monotonic=monotonic,
                    sleep=sleep, temp_root=root,
                    terminate=lambda proc: terminated.append(proc.pid), process_identity=lambda pid: (pid, pid, 123456))
                try:
                    with self.assertRaisesRegex(TimeoutError, 'timeout inferencji'):
                        runner({'path': str(model), 'sha256': sha}, 1, lambda: False)
                    self.assertTrue(inference_started.is_set())
                finally:
                    release_inference.set()
            self.assertEqual(terminated, [process.pid])
            self.assertTrue(created_cwd)
            self.assertFalse(Path(created_cwd[0]).exists())


if __name__ == '__main__':
    unittest.main()
