"""Fail-closed, explicit runtime-test job queue.

This module does not auto-start workers and has no default model launcher.
A trusted, model-specific runner must be injected by a future integration.
Every execution rechecks the exact TEST_ONLY grant, quarantine record, hardware
preflight and SHA-256 immediately before handing the artifact to that runner.
"""
from __future__ import annotations

import fcntl
import json
import os
import signal
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
        error TEXT,
        batch_id INTEGER,
        process_pid INTEGER,
        process_start_ticks INTEGER
    )''')
    columns = {row[1] for row in connection.execute('PRAGMA table_info(runtime_test_jobs)')}
    if 'created_by' not in columns:
        connection.execute("ALTER TABLE runtime_test_jobs ADD COLUMN created_by TEXT NOT NULL DEFAULT ''")
    if 'batch_id' not in columns:
        connection.execute("ALTER TABLE runtime_test_jobs ADD COLUMN batch_id INTEGER")
    if 'process_pid' not in columns:
        connection.execute("ALTER TABLE runtime_test_jobs ADD COLUMN process_pid INTEGER")
    if 'process_start_ticks' not in columns:
        connection.execute("ALTER TABLE runtime_test_jobs ADD COLUMN process_start_ticks INTEGER")
    connection.execute('CREATE INDEX IF NOT EXISTS idx_runtime_test_jobs_status ON runtime_test_jobs(status,id)')
    connection.execute('''CREATE TABLE IF NOT EXISTS runtime_test_batches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        status TEXT NOT NULL CHECK(status IN
          ('pending_approval','approved','running','completed','failed','cancelled','revoked','expired')),
        created_by TEXT NOT NULL,
        created_at TEXT NOT NULL,
        approved_by TEXT,
        approved_at TEXT,
        expires_at TEXT,
        revoked_by TEXT,
        revoked_at TEXT,
        reason TEXT NOT NULL DEFAULT ''
    )''')
    connection.execute('''CREATE TABLE IF NOT EXISTS runtime_execution_settings (
        id INTEGER PRIMARY KEY CHECK(id=1),
        enabled INTEGER NOT NULL DEFAULT 0,
        changed_by TEXT,
        changed_at TEXT,
        reason TEXT NOT NULL DEFAULT ''
    )''')
    connection.execute("INSERT OR IGNORE INTO runtime_execution_settings(id,enabled) VALUES(1,0)")


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


def set_execution_enabled(*, enabled: bool, confirm: bool, actor: str,
                          reason: str = '') -> dict:
    model_permissions._validate_actor(actor)
    if not isinstance(enabled, bool) or confirm is not True:
        raise ValueError('Wymagane jawne potwierdzenie ustawienia runtime.')
    if enabled and os.environ.get('ARENA_RUNTIME_EXECUTION_ENABLED') != '1':
        raise PermissionError('Brak niezależnego opt-in ARENA_RUNTIME_EXECUTION_ENABLED=1; runtime pozostaje wyłączony.')
    if not isinstance(reason, str) or len(reason.strip()) < 3:
        raise ValueError('Podaj uzasadnienie zmiany (co najmniej 3 znaki).')
    with league.connect() as connection:
        _init(connection)
        connection.execute('''UPDATE runtime_execution_settings
            SET enabled=?,changed_by=?,changed_at=?,reason=? WHERE id=1''',
            (int(enabled), actor, _now(), reason.strip()))
        if not enabled:
            connection.execute("UPDATE runtime_test_batches SET status='revoked',revoked_by=?,revoked_at=?,reason=? WHERE status IN ('pending_approval','approved','running')",
                               (actor, _now(), reason.strip()))
            connection.execute("UPDATE runtime_test_jobs SET cancel_requested=1 WHERE status='running'")
            connection.execute("UPDATE runtime_test_jobs SET status='cancelled',finished_at=?,error=? WHERE status='queued'",
                               (_now(), 'Runtime został wyłączony przez operatora.',))
    return {'enabled': enabled, 'changed_by': actor, 'changed_at': _now()}


def create_batch(*, job_ids: list[int], actor: str) -> dict:
    model_permissions._validate_actor(actor)
    if not isinstance(job_ids, list) or not job_ids or len(job_ids) > 100:
        raise ValueError('Partia musi zawierać od 1 do 100 zadań.')
    if any(isinstance(item, bool) or not isinstance(item, int) or item < 1 for item in job_ids):
        raise ValueError('Nieprawidłowy identyfikator zadania w partii.')
    if len(set(job_ids)) != len(job_ids):
        raise ValueError('Lista zadań zawiera duplikaty.')
    with league.connect() as connection:
        _init(connection)
        placeholders = ','.join('?' for _ in job_ids)
        rows = connection.execute(
            f'SELECT id,status,batch_id FROM runtime_test_jobs WHERE id IN ({placeholders})',
            tuple(job_ids)).fetchall()
        if len(rows) != len(job_ids) or any(row['status'] != 'queued' or row['batch_id'] is not None for row in rows):
            raise ValueError('Wszystkie zadania muszą być oczekujące i nie mogą należeć do innej partii.')
        cursor = connection.execute('''INSERT INTO runtime_test_batches
            (status,created_by,created_at) VALUES('pending_approval',?,?)''', (actor, _now()))
        batch_id = cursor.lastrowid
        changed = connection.execute(
            f"UPDATE runtime_test_jobs SET batch_id=? WHERE id IN ({placeholders}) AND status='queued' AND batch_id IS NULL",
            (batch_id, *job_ids))
        if changed.rowcount != len(job_ids):
            raise RuntimeError('Nie udało się atomowo przypisać wszystkich zadań do partii.')
    return {'batch_id': batch_id, 'status': 'pending_approval', 'job_ids': job_ids,
            'runtime_started': False}


def approve_batch(*, batch_id: int, confirm: bool, actor: str,
                  reason: str, approval_seconds: int = 600) -> dict:
    model_permissions._validate_actor(actor)
    if isinstance(batch_id, bool) or not isinstance(batch_id, int) or batch_id < 1:
        raise ValueError('Nieprawidłowy identyfikator partii.')
    if confirm is not True:
        raise ValueError('Wymagane jawne potwierdzenie uruchomienia tej partii.')
    if not isinstance(reason, str) or len(reason.strip()) < 3:
        raise ValueError('Podaj uzasadnienie zatwierdzenia (co najmniej 3 znaki).')
    if isinstance(approval_seconds, bool) or not isinstance(approval_seconds, int) or not 30 <= approval_seconds <= 600:
        raise ValueError('Zatwierdzenie może być ważne od 30 do 600 sekund.')
    now_epoch = int(time.time())
    expires = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(now_epoch + approval_seconds))
    with league.connect() as connection:
        _init(connection)
        settings = connection.execute('SELECT enabled FROM runtime_execution_settings WHERE id=1').fetchone()
        if not settings or not settings['enabled'] or os.environ.get('ARENA_RUNTIME_EXECUTION_ENABLED') != '1':
            raise PermissionError('Runtime nie jest niezależnie włączony; partia nie może zostać zatwierdzona.')
        batch = connection.execute('SELECT status FROM runtime_test_batches WHERE id=?', (batch_id,)).fetchone()
        jobs = connection.execute("SELECT COUNT(*) AS total FROM runtime_test_jobs WHERE batch_id=? AND status='queued'", (batch_id,)).fetchone()
        if not batch or batch['status'] != 'pending_approval' or not jobs or jobs['total'] < 1:
            raise ValueError('Partia nie istnieje, nie oczekuje na zatwierdzenie lub nie ma zadań.')
        changed = connection.execute('''UPDATE runtime_test_batches SET status='approved',
            approved_by=?,approved_at=?,expires_at=?,reason=? WHERE id=? AND status='pending_approval' ''',
            (actor, _now(), expires, reason.strip(), batch_id))
        if changed.rowcount != 1:
            raise ValueError('Partia została zmieniona równolegle; odśwież panel.')
    return {'batch_id': batch_id, 'status': 'approved', 'approved_by': actor,
            'expires_at': expires, 'runtime_started': False}


def revoke_batch(*, batch_id: int, actor: str, reason: str) -> dict:
    model_permissions._validate_actor(actor)
    if not isinstance(reason, str) or len(reason.strip()) < 3:
        raise ValueError('Podaj uzasadnienie wycofania partii.')
    with league.connect() as connection:
        _init(connection)
        connection.commit()
        connection.execute('BEGIN IMMEDIATE')
        batch = connection.execute('SELECT status FROM runtime_test_batches WHERE id=?', (batch_id,)).fetchone()
        if not batch or batch['status'] not in {'pending_approval','approved','running'}:
            raise ValueError('Nie można wycofać tej partii.')
        connection.execute('''UPDATE runtime_test_batches SET status='revoked',
            revoked_by=?,revoked_at=?,reason=? WHERE id=?''',
            (actor, _now(), reason.strip(), batch_id))
        connection.execute('''UPDATE runtime_test_jobs SET cancel_requested=1
            WHERE batch_id=? AND status='running' ''', (batch_id,))
        connection.execute('''UPDATE runtime_test_jobs SET status='cancelled',finished_at=?,error=?
            WHERE batch_id=? AND status='queued' ''', (_now(), 'Partia została wycofana.', batch_id))
    return {'batch_id': batch_id, 'status': 'revoked'}



def _expire_batches(connection: sqlite3.Connection) -> None:
    now = _now()
    expired = connection.execute(
        "SELECT id FROM runtime_test_batches WHERE status='approved' AND expires_at IS NOT NULL AND expires_at<=?",
        (now,)).fetchall()
    ids = [row['id'] for row in expired]
    if not ids:
        return
    placeholders = ','.join('?' for _ in ids)
    connection.execute(
        f"UPDATE runtime_test_batches SET status='expired' WHERE id IN ({placeholders}) AND status='approved'",
        tuple(ids))
    connection.execute(
        f"UPDATE runtime_test_jobs SET status='cancelled',finished_at=?,error=? WHERE batch_id IN ({placeholders}) AND status='queued'",
        (_now(), 'Wygasło zatwierdzenie partii runtime.', *ids))



def _process_identity(pid: int) -> dict | None:
    """Read Linux procfs identity fields needed to avoid killing a reused PID."""
    try:
        raw = Path(f'/proc/{pid}/stat').read_text()
        close = raw.rfind(')')
        if close < 0:
            return None
        fields = raw[close + 2:].split()
        return {
            'pid': pid, 'state': fields[0], 'pgrp': int(fields[2]),
            'session': int(fields[3]), 'start_ticks': int(fields[19]),
            'cmdline': Path(f'/proc/{pid}/cmdline').read_bytes().replace(b'\0', b' ').decode('utf-8', 'replace'),
            'exe': os.readlink(f'/proc/{pid}/exe'),
        }
    except (OSError, ValueError, IndexError):
        return None


def _terminate_verified_orphan(pid: int, start_ticks: int) -> bool:
    identity = _process_identity(pid)
    if not identity:
        return False
    if (identity['start_ticks'] != start_ticks or identity['pgrp'] != pid
            or identity['session'] != pid):
        return False
    trusted = ('/home/debian/ai-agent/bin/llamafile' in identity['cmdline']
               or identity['exe'] == '/home/debian/ai-agent/bin/llamafile'
               or 'runtime_worker.py' in identity['cmdline'])
    if not trusted:
        return False
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        if _process_identity(pid) is None:
            return True
        try:
            os.killpg(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.1)
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        return True
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        try:
            os.killpg(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.1)
    return False


def recover_orphaned_jobs() -> dict:
    """Fail stale running jobs after restart; kill only a verified owned process group."""
    with league.connect() as connection:
        _init(connection)
        rows = connection.execute(
            "SELECT id,batch_id,process_pid,process_start_ticks FROM runtime_test_jobs WHERE status='running'"
        ).fetchall()
    recovered = []
    for row in rows:
        pid, start_ticks = row['process_pid'], row['process_start_ticks']
        terminated = False
        if (isinstance(pid, int) and pid > 1 and isinstance(start_ticks, int)
                and start_ticks > 0):
            terminated = _terminate_verified_orphan(pid, start_ticks)
        reason = ('Po restarcie serwera zakończono osierocony proces runtime.'
                  if terminated else
                  'Po restarcie serwera zadanie nie ma aktywnego, zweryfikowanego procesu; nie wysłano sygnału do obcego PID.')
        with league.connect() as connection:
            connection.execute(
                "UPDATE runtime_test_jobs SET status='failed',finished_at=?,error=? WHERE id=? AND status='running'",
                (_now(), reason, row['id']))
            if row['batch_id'] is not None:
                remaining = connection.execute(
                    "SELECT COUNT(*) AS total FROM runtime_test_jobs WHERE batch_id=? AND status='queued'",
                    (row['batch_id'],)).fetchone()['total']
                running = connection.execute(
                    "SELECT COUNT(*) AS total FROM runtime_test_jobs WHERE batch_id=? AND status='running'",
                    (row['batch_id'],)).fetchone()['total']
                if not remaining and not running:
                    connection.execute(
                        "UPDATE runtime_test_batches SET status='failed' WHERE id=? AND status='running'",
                        (row['batch_id'],))
        recovered.append({'job_id': row['id'], 'status': 'failed',
                          'process_terminated': terminated, 'reason': reason})
    return {'recovered': recovered, 'count': len(recovered)}


def snapshot() -> dict:
    with league.connect() as connection:
        _init(connection)
        _expire_batches(connection)
        rows = connection.execute('''SELECT id,model_id,repo_id,revision,artifact_key,
            artifact_sha256,permission_request_id,created_by,status,timeout_seconds,queued_at,
            started_at,finished_at,cancel_requested,error,batch_id,process_pid,process_start_ticks
            FROM runtime_test_jobs ORDER BY id DESC''').fetchall()
        batches = connection.execute('SELECT * FROM runtime_test_batches ORDER BY id DESC').fetchall()
        settings = connection.execute('SELECT * FROM runtime_execution_settings WHERE id=1').fetchone()
    return {'jobs': [dict(row) for row in rows], 'batches': [dict(row) for row in batches],
            'execution_enabled': bool(settings['enabled']) if settings else False,
            'environment_opt_in': os.environ.get('ARENA_RUNTIME_EXECUTION_ENABLED') == '1',
            'auto_start': False, 'max_parallel_models': 1, 'runtime_started': False}


def _persist_authorized_result(*, job_id: int, batch_id: int,
                              permission_request_id: int, result: dict) -> bool:
    """Commit a result only if cancellation and every execution grant still hold.

    BEGIN IMMEDIATE serializes this terminal transition against cancel, batch
    revocation, permission revocation and global disable. Whichever transaction
    commits first determines the terminal outcome; a late result cannot overwrite
    a cancellation that already won the race.
    """
    if os.environ.get('ARENA_RUNTIME_EXECUTION_ENABLED') != '1':
        return False
    encoded = json.dumps(result, ensure_ascii=False, sort_keys=True)
    with league.connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        job = connection.execute(
            "SELECT status,cancel_requested,batch_id,permission_request_id,"
            "model_id,repo_id,revision,artifact_sha256 "
            "FROM runtime_test_jobs WHERE id=?", (job_id,)
        ).fetchone()
        batch = connection.execute(
            "SELECT status FROM runtime_test_batches WHERE id=?", (batch_id,)
        ).fetchone()
        grant = connection.execute(
            "SELECT status,scope,model_id,repo_id,revision,artifact_sha256 "
            "FROM model_permission_requests WHERE id=?",
            (permission_request_id,),
        ).fetchone()
        settings = connection.execute(
            "SELECT enabled FROM runtime_execution_settings WHERE id=1"
        ).fetchone()
        if (not job or job['status'] != 'running' or bool(job['cancel_requested'])
                or job['batch_id'] != batch_id
                or job['permission_request_id'] != permission_request_id
                or not batch or batch['status'] != 'running'
                or not grant or grant['status'] != 'approved'
                or grant['scope'] != 'TEST_ONLY'
                or (grant['model_id'], grant['repo_id'], grant['revision'],
                    grant['artifact_sha256']) != (
                        job['model_id'], job['repo_id'], job['revision'],
                        job['artifact_sha256'])
                or not settings or not settings['enabled']):
            return False
        changed = connection.execute(
            "UPDATE runtime_test_jobs SET status='completed',finished_at=?,result_json=? "
            "WHERE id=? AND status='running' AND cancel_requested=0",
            (_now(), encoded, job_id),
        )
        return changed.rowcount == 1


def cancel(job_id: int) -> dict:
    if isinstance(job_id, bool) or not isinstance(job_id, int) or job_id < 1:
        raise ValueError('Nieprawidłowy identyfikator zadania.')
    with league.connect() as connection:
        _init(connection)
        connection.commit()
        # Serialize the status read with claim/start and terminal result commit.
        connection.execute('BEGIN IMMEDIATE')
        row = connection.execute('SELECT status FROM runtime_test_jobs WHERE id=?', (job_id,)).fetchone()
        if not row:
            raise ValueError('Nie znaleziono zadania.')
        if row['status'] == 'queued':
            changed = connection.execute(
                "UPDATE runtime_test_jobs SET status='cancelled',finished_at=? WHERE id=? AND status='queued'",
                (_now(), job_id),
            )
            if changed.rowcount == 1:
                return {'job_id': job_id, 'status': 'cancelled'}
        elif row['status'] == 'running':
            changed = connection.execute(
                "UPDATE runtime_test_jobs SET cancel_requested=1 WHERE id=? AND status='running'",
                (job_id,),
            )
            if changed.rowcount == 1:
                return {'job_id': job_id, 'status': 'cancel_requested'}
        current = connection.execute(
            'SELECT status,cancel_requested FROM runtime_test_jobs WHERE id=?', (job_id,)
        ).fetchone()
        if current['status'] == 'running' and current['cancel_requested']:
            return {'job_id': job_id, 'status': 'cancel_requested'}
        return {'job_id': job_id, 'status': current['status']}



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
                _expire_batches(connection)
                row = connection.execute('SELECT * FROM runtime_test_jobs WHERE id=?', (job_id,)).fetchone()
                if not row or row['status'] != 'queued':
                    raise ValueError('Można uruchomić wyłącznie zadanie oczekujące.')
                job = dict(row)
                if job.get('batch_id') is None:
                    raise PermissionError('Zadanie nie należy do jawnie zatwierdzonej partii runtime.')
                settings = connection.execute('SELECT enabled FROM runtime_execution_settings WHERE id=1').fetchone()
                batch = connection.execute('SELECT * FROM runtime_test_batches WHERE id=?', (job['batch_id'],)).fetchone()
                if (not settings or not settings['enabled']
                        or os.environ.get('ARENA_RUNTIME_EXECUTION_ENABLED') != '1'):
                    raise PermissionError('Runtime jest wyłączony; wymagane niezależne opt-in i jawne włączenie.')
                if (not batch or batch['status'] not in {'approved','running'}
                        or not batch['expires_at']
                        or (batch['status'] == 'approved' and batch['expires_at'] <= _now())):
                    raise PermissionError('Brak ważnego, jawnego zatwierdzenia tej partii runtime.')
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
                connection.execute("UPDATE runtime_test_batches SET status='running' WHERE id=? AND status='approved'",
                                   (job['batch_id'],))
                if changed.rowcount != 1:
                    raise RuntimeError('Zadanie zostało przejęte równolegle.')

            # This refreshes hardware/provenance and re-hashes the exact quarantine file.
            handoff = hf_candidate_runtime.authorize_runtime_handoff(job['artifact_key'], explicit_approval=True)
            if not handoff.get('ok') or handoff.get('sha256') != job['artifact_sha256']:
                raise PermissionError(handoff.get('reason') or 'SHA-256 nie odpowiada zgodzie TEST_ONLY.')

            def register_process(pid: int, pgrp: int, start_ticks: int) -> None:
                if (isinstance(pid, bool) or not isinstance(pid, int) or pid <= 1
                        or pgrp != pid or isinstance(start_ticks, bool)
                        or not isinstance(start_ticks, int) or start_ticks <= 0):
                    raise ValueError('Nieprawidłowa tożsamość procesu runtime.')
                with league.connect() as connection:
                    changed = connection.execute(
                        "UPDATE runtime_test_jobs SET process_pid=?,process_start_ticks=? WHERE id=? AND status='running' AND cancel_requested=0",
                        (pid, start_ticks, job_id))
                if changed.rowcount != 1:
                    raise InterruptedError('Zadanie anulowano przed rejestracją procesu runtime.')

            def cancellation_requested() -> bool:
                with league.connect() as connection:
                    current = connection.execute('SELECT cancel_requested FROM runtime_test_jobs WHERE id=?', (job_id,)).fetchone()
                    grant_now = connection.execute('SELECT status FROM model_permission_requests WHERE id=?',
                                                   (job['permission_request_id'],)).fetchone()
                    batch_now = connection.execute('SELECT status,expires_at FROM runtime_test_batches WHERE id=?',
                                                   (job['batch_id'],)).fetchone()
                    settings_now = connection.execute('SELECT enabled FROM runtime_execution_settings WHERE id=1').fetchone()
                return (not current or bool(current['cancel_requested']) or not grant_now
                        or grant_now['status'] != 'approved' or not batch_now
                        or batch_now['status'] not in {'approved','running'}
                        or not settings_now or not settings_now['enabled']
                        or os.environ.get('ARENA_RUNTIME_EXECUTION_ENABLED') != '1')

            if cancellation_requested():
                raise InterruptedError('Zadanie anulowano przed przekazaniem do runtime.')
            payload = {**job, 'path': handoff['path'], 'sha256': handoff['sha256'],
                       'size_bytes': handoff['size_bytes'], 'register_process': register_process}
            result = runner(payload, job['timeout_seconds'], cancellation_requested)
            if not isinstance(result, dict):
                raise TypeError('Adapter runtime musi zwrócić obiekt wyniku.')
            if cancellation_requested():
                raise InterruptedError('Zgoda została wycofana lub zadanie anulowano podczas testu.')
            if not _persist_authorized_result(
                    job_id=job_id, batch_id=job['batch_id'],
                    permission_request_id=job['permission_request_id'], result=result):
                raise InterruptedError(
                    'Cancellation, permission revocation or runtime disable won the result-commit race.'
                )
            with league.connect() as connection:
                remaining = connection.execute(
                    "SELECT COUNT(*) AS total FROM runtime_test_jobs WHERE batch_id=? AND status='queued'",
                    (job['batch_id'],)).fetchone()['total']
                running = connection.execute(
                    "SELECT COUNT(*) AS total FROM runtime_test_jobs WHERE batch_id=? AND status='running'",
                    (job['batch_id'],)).fetchone()['total']
                if not remaining and not running:
                    failed = connection.execute(
                        "SELECT COUNT(*) AS total FROM runtime_test_jobs WHERE batch_id=? AND status='failed'",
                        (job['batch_id'],)).fetchone()['total']
                    cancelled = connection.execute(
                        "SELECT COUNT(*) AS total FROM runtime_test_jobs WHERE batch_id=? AND status='cancelled'",
                        (job['batch_id'],)).fetchone()['total']
                    terminal_status = 'failed' if failed else ('cancelled' if cancelled else 'completed')
                    connection.execute("UPDATE runtime_test_batches SET status=? WHERE id=? AND status='running'",
                                       (terminal_status, job['batch_id']))
            return {'job_id': job_id, 'status': 'completed', 'result': result}
        except InterruptedError as exc:
            with league.connect() as connection:
                connection.execute("UPDATE runtime_test_jobs SET status='cancelled',finished_at=?,error=? WHERE id=? AND status='running'",
                                   (_now(), str(exc)[:2000], job_id))
                if 'job' in locals() and job.get('batch_id') is not None:
                    remaining = connection.execute(
                        "SELECT COUNT(*) AS total FROM runtime_test_jobs WHERE batch_id=? AND status='queued'",
                        (job['batch_id'],)).fetchone()['total']
                    running = connection.execute(
                        "SELECT COUNT(*) AS total FROM runtime_test_jobs WHERE batch_id=? AND status='running'",
                        (job['batch_id'],)).fetchone()['total']
                    if not remaining and not running:
                        connection.execute("UPDATE runtime_test_batches SET status='cancelled' WHERE id=? AND status='running'",
                                           (job['batch_id'],))
            return {'job_id': job_id, 'status': 'cancelled', 'error': str(exc)}
        except Exception as exc:
            with league.connect() as connection:
                # Fail a queued job too when its pre-execution permission or
                # quarantine checks fail; otherwise it would remain queued forever.
                connection.execute("UPDATE runtime_test_jobs SET status='failed',finished_at=?,error=? WHERE id=? AND status IN ('queued','running')",
                                   (_now(), str(exc)[:2000], job_id))
                if 'job' in locals() and job.get('batch_id') is not None:
                    remaining = connection.execute(
                        "SELECT COUNT(*) AS total FROM runtime_test_jobs WHERE batch_id=? AND status='queued'",
                        (job['batch_id'],)).fetchone()['total']
                    running = connection.execute(
                        "SELECT COUNT(*) AS total FROM runtime_test_jobs WHERE batch_id=? AND status='running'",
                        (job['batch_id'],)).fetchone()['total']
                    if not remaining and not running:
                        connection.execute("UPDATE runtime_test_batches SET status='failed' WHERE id=? AND status='running'",
                                           (job['batch_id'],))
            raise
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
