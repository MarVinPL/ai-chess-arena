"""Durable coordinator-owned worker job state; never infer idle from an empty DB.

Only the trusted scheduler may call record_assignment/record_terminal and
confirm_idle. No worker-facing HTTP endpoint mutates this registry.
"""
from __future__ import annotations

import re
import sqlite3
import time
from pathlib import Path

_ID = re.compile(r'[A-Za-z0-9_-]{8,96}\Z')


class WorkerJobRegistry:
    def __init__(self, path: str | Path, *, clock=time.time):
        self.path = str(path)
        self.clock = clock
        with self._connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS remote_worker_job_state (
                worker_id TEXT PRIMARY KEY, job_id TEXT, state TEXT NOT NULL,
                deadline REAL, updated_at REAL NOT NULL)''')

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.execute('PRAGMA busy_timeout=10000')
        return db

    @staticmethod
    def _validate(worker_id: str, job_id: str | None = None):
        if not isinstance(worker_id, str) or not _ID.fullmatch(worker_id):
            raise ValueError('Invalid worker ID')
        if job_id is not None and (not isinstance(job_id, str) or not _ID.fullmatch(job_id)):
            raise ValueError('Invalid job ID')

    def record_assignment(self, worker_id: str, job_id: str, *, deadline: float):
        self._validate(worker_id, job_id)
        if not isinstance(deadline, (int, float)) or isinstance(deadline, bool) or deadline <= self.clock():
            raise ValueError('Invalid deadline')
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT state FROM remote_worker_job_state WHERE worker_id=?', (worker_id,)).fetchone()
            if row and row[0] != 'idle_confirmed':
                raise ValueError('Worker has unresolved job state')
            db.execute('''INSERT INTO remote_worker_job_state VALUES (?, ?, 'running', ?, ?)
                ON CONFLICT(worker_id) DO UPDATE SET job_id=excluded.job_id,
                state='running', deadline=excluded.deadline, updated_at=excluded.updated_at''',
                (worker_id, job_id, deadline, self.clock()))

    def record_terminal(self, worker_id: str, job_id: str):
        self._validate(worker_id, job_id)
        with self._connect() as db:
            result = db.execute('''UPDATE remote_worker_job_state
                SET state='idle_confirmed', job_id=NULL, deadline=NULL, updated_at=?
                WHERE worker_id=? AND job_id=? AND state='running' ''',
                (self.clock(), worker_id, job_id))
            if result.rowcount != 1:
                raise ValueError('Unrecognized or already completed job')

    def recover_after_restart(self):
        """Invalidate stale claims before the HTTP listener accepts traffic."""
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("""UPDATE remote_worker_job_state
                SET state='unknown', job_id=NULL, deadline=NULL, updated_at=?""",
                (self.clock(),))

    def state(self, worker_id: str) -> str:
        self._validate(worker_id)
        with self._connect() as db:
            row = db.execute('SELECT state, deadline FROM remote_worker_job_state WHERE worker_id=?', (worker_id,)).fetchone()
        if row is None:
            return 'unknown'
        if row[0] == 'running' and (row[1] is None or row[1] <= self.clock()):
            return 'unknown'
        return row[0] if row[0] in ('running', 'idle_confirmed') else 'unknown'
