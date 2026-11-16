"""Fail-closed, explicit runtime-test job queue.

This module does not auto-start workers and has no default model launcher.
A trusted, model-specific runner must be injected by a future integration.
Every execution rechecks the exact TEST_ONLY grant, quarantine record, hardware
preflight and SHA-256 immediately before handing the artifact to that runner.
"""
from __future__ import annotations

import fcntl
import json
import sqlite3
import time
from pathlib import Path
from typing import Callable

import hf_candidate_runtime
import league
import model_permissions

DEFAULT_TIMEOUT_SECONDS = 300
MAX_TIMEOUT_SECONDS = 1800
LOCK_PATH = Path('/run/lock/ai-chess-arena-runtime-test.lock')


def _now() -> str:
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def _init(connection: sqlite3.Connection) -> None:
    connection.execute('''CREATE TABLE IF NOT EXISTS runtime_test_jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        model_id INTEGER NOT NULL,
        repo_id TEXT NOT NULL,
        revision TEXT NOT NULL,
        artifact_key TEXT NOT NULL,
        artifact_sha256 TEXT NOT NULL,
        permission_request_id INTEGER NOT NULL,
        created_by TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL CHECK(status IN
          ('queued','running','completed','failed','cancelled','blocked')),
        timeout_seconds INTEGER NOT NULL,
        queued_at TEXT NOT NULL,
        started_at TEXT,
        finished_at TEXT,
        cancel_requested INTEGER NOT NULL DEFAULT 0,
        result_json TEXT,
        error TEXT
    )''')
    columns = {row[1] for row in connection.execute('PRAGMA table_info(runtime_test_jobs)')}
    if 'created_by' not in columns:
        connection.execute("ALTER TABLE runtime_test_jobs ADD COLUMN created_by TEXT NOT NULL DEFAULT ''")
    connection.execute('CREATE INDEX IF NOT EXISTS idx_runtime_test_jobs_status ON runtime_test_jobs(status,id)')


def _validate_timeout(timeout_seconds: int) -> int:
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int):
        raise ValueError('Timeout musi być liczbą całkowitą.')
    if not 1 <= timeout_seconds <= MAX_TIMEOUT_SECONDS:
        raise ValueError(f'Timeout musi mieścić się w zakresie 1–{MAX_TIMEOUT_SECONDS} sekund.')
    return timeout_seconds


def enqueue(*, model_id: int, repo_id: str, revision: str, artifact_sha256: str,
            artifact_key: str, permission_request_id: int,
            timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
            actor: str | None = None) -> dict:
    """Create a queued test only when an exact, already-approved TEST_ONLY grant exists."""
    model_permissions._validate_actor(actor)
    timeout_seconds = _validate_timeout(timeout_seconds)
    normalized_repo = model_permissions._validate_identity(
        model_id, repo_id, revision, artifact_sha256)
    if not isinstance(artifact_key, str) or artifact_key.count('|') != 2:
        raise ValueError('Nieprawidłowy klucz artefaktu.')
    catalog_id, key_revision, filename = artifact_key.split('|', 2)
    if key_revision != revision or not catalog_id or not filename:
        raise ValueError('Klucz artefaktu nie odpowiada przypiętej rewizji.')

    with league.connect() as connection:
        model_permissions.ensure_schema(connection)
        _init(connection)
        grant = connection.execute(
            'SELECT * FROM model_permission_requests WHERE id=?',
            (permission_request_id,),
        ).fetchone()
        if not grant or grant['status'] != 'approved' or grant['scope'] != 'TEST_ONLY':
            raise ValueError('Wymagana jest aktywna, zatwierdzona zgoda TEST_ONLY.')
        if (grant['model_id'], grant['repo_id'], grant['revision'], grant['artifact_sha256']) != (
                model_id, normalized_repo, revision, artifact_sha256):
            raise ValueError('Zgoda TEST_ONLY nie pasuje dokładnie do modelu, repo, rewizji i SHA-256.')
        if not model_permissions.has_permission(
                connection, model_id=model_id, repo_id=normalized_repo,
                revision=revision, artifact_sha256=artifact_sha256, scope='TEST_ONLY'):
            raise ValueError('Brak aktywnego uprawnienia TEST_ONLY.')
        cursor = connection.execute('''INSERT INTO runtime_test_jobs
            (model_id,repo_id,revision,artifact_key,artifact_sha256,
             permission_request_id,created_by,status,timeout_seconds,queued_at)
            VALUES(?,?,?,?,?,?,?,'queued',?,?)''',
            (model_id, normalized_repo, revision, artifact_key, artifact_sha256,
             permission_request_id, actor, timeout_seconds, _now()))
        return {'job_id': cursor.lastrowid, 'status': 'queued',
                'runtime_started': False, 'auto_start': False}


def snapshot() -> dict:
    with league.connect() as connection:
        _init(connection)
        rows = connection.execute('''SELECT id,model_id,repo_id,revision,artifact_key,
            artifact_sha256,permission_request_id,created_by,status,timeout_seconds,queued_at,
            started_at,finished_at,cancel_requested,error
            FROM runtime_test_jobs ORDER BY id DESC''').fetchall()
    return {'jobs': [dict(row) for row in rows], 'auto_start': False,
            'max_parallel_models': 1, 'runtime_started': False}


def cancel(job_id: int) -> dict:
    if isinstance(job_id, bool) or not isinstance(job_id, int) or job_id < 1:
        raise ValueError('Nieprawidłowy identyfikator zadania.')
    with league.connect() as connection:
        _init(connection)
        row = connection.execute('SELECT status FROM runtime_test_jobs WHERE id=?', (job_id,)).fetchone()
        if not row:
            raise ValueError('Nie znaleziono zadania.')
        if row['status'] == 'queued':
            connection.execute("UPDATE runtime_test_jobs SET status='cancelled',finished_at=? WHERE id=? AND status='queued'", (_now(), job_id))
            return {'job_id': job_id, 'status': 'cancelled'}
        if row['status'] == 'running':
            connection.execute('UPDATE runtime_test_jobs SET cancel_requested=1 WHERE id=? AND status=\'running\'', (job_id,))
            return {'job_id': job_id, 'status': 'cancel_requested'}
        return {'job_id': job_id, 'status': row['status']}


def execute_job(job_id: int, *, runner: Callable[[dict, int, Callable[[], bool]], dict] | None = None,
                lock_path: Path = LOCK_PATH) -> dict:
    """Execute exactly one approved job; no default runner means no process can start."""
    if runner is None:
        raise RuntimeError('Brak jawnie skonfigurowanego, zaufanego adaptera runtime; zadanie pozostaje w kolejce.')
    if isinstance(job_id, bool) or not isinstance(job_id, int) or job_id < 1:
        raise ValueError('Nieprawidłowy identyfikator zadania.')

    lock_path = Path(lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open('a+') as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError('Inny test runtime jest już aktywny; globalna blokada zajęta.') from exc
        try:
            with league.connect() as connection:
                _init(connection)
                row = connection.execute('SELECT * FROM runtime_test_jobs WHERE id=?', (job_id,)).fetchone()
                if not row or row['status'] != 'queued':
                    raise ValueError('Można uruchomić wyłącznie zadanie oczekujące.')
                job = dict(row)
                grant = connection.execute('SELECT * FROM model_permission_requests WHERE id=?',
                                           (job['permission_request_id'],)).fetchone()
                if not grant or grant['status'] != 'approved' or grant['scope'] != 'TEST_ONLY':
                    raise PermissionError('Zgoda TEST_ONLY została wycofana lub jest nieaktywna.')
                if (grant['model_id'], grant['repo_id'], grant['revision'], grant['artifact_sha256']) != (
                        job['model_id'], job['repo_id'], job['revision'], job['artifact_sha256']):
                    raise PermissionError('Zapisana zgoda nie odpowiada zadaniu.')
                if not model_permissions.has_permission(
                        connection, model_id=job['model_id'], repo_id=job['repo_id'],
                        revision=job['revision'], artifact_sha256=job['artifact_sha256'], scope='TEST_ONLY'):
                    raise PermissionError('Brak aktywnego uprawnienia TEST_ONLY.')
                changed = connection.execute("UPDATE runtime_test_jobs SET status='running',started_at=?,error=NULL WHERE id=? AND status='queued'",
                                             (_now(), job_id))
                if changed.rowcount != 1:
                    raise RuntimeError('Zadanie zostało przejęte równolegle.')

            # This refreshes hardware/provenance and re-hashes the exact quarantine file.
            handoff = hf_candidate_runtime.authorize_runtime_handoff(job['artifact_key'], explicit_approval=True)
            if not handoff.get('ok') or handoff.get('sha256') != job['artifact_sha256']:
                raise PermissionError(handoff.get('reason') or 'SHA-256 nie odpowiada zgodzie TEST_ONLY.')

            def cancellation_requested() -> bool:
                with league.connect() as connection:
                    current = connection.execute('SELECT cancel_requested FROM runtime_test_jobs WHERE id=?', (job_id,)).fetchone()
                    grant_now = connection.execute('SELECT status FROM model_permission_requests WHERE id=?',
                                                   (job['permission_request_id'],)).fetchone()
                return (not current or bool(current['cancel_requested']) or not grant_now
                        or grant_now['status'] != 'approved')

            if cancellation_requested():
                raise InterruptedError('Zadanie anulowano przed przekazaniem do runtime.')
            payload = {**job, 'path': handoff['path'], 'sha256': handoff['sha256'],
                       'size_bytes': handoff['size_bytes']}
            result = runner(payload, job['timeout_seconds'], cancellation_requested)
            if not isinstance(result, dict):
                raise TypeError('Adapter runtime musi zwrócić obiekt wyniku.')
            if cancellation_requested():
                raise InterruptedError('Zgoda została wycofana lub zadanie anulowano podczas testu.')
            with league.connect() as connection:
                connection.execute("UPDATE runtime_test_jobs SET status='completed',finished_at=?,result_json=? WHERE id=? AND status='running'",
                                   (_now(), json.dumps(result, ensure_ascii=False, sort_keys=True), job_id))
            return {'job_id': job_id, 'status': 'completed', 'result': result}
        except InterruptedError as exc:
            with league.connect() as connection:
                connection.execute("UPDATE runtime_test_jobs SET status='cancelled',finished_at=?,error=? WHERE id=? AND status='running'",
                                   (_now(), str(exc)[:2000], job_id))
            return {'job_id': job_id, 'status': 'cancelled', 'error': str(exc)}
        except Exception as exc:
            with league.connect() as connection:
                # Fail a queued job too when its pre-execution permission or
                # quarantine checks fail; otherwise it would remain queued forever.
                connection.execute("UPDATE runtime_test_jobs SET status='failed',finished_at=?,error=? WHERE id=? AND status IN ('queued','running')",
                                   (_now(), str(exc)[:2000], job_id))
            raise
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
