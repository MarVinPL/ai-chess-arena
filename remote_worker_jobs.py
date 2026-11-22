"""Durable coordinator-owned update safety state; no worker-side writes.

Only coordinator-validated transitions may modify this registry. Missing rows,
expired leases and interrupted tasks fail closed. This is not a job scheduler.
"""
import re
import sqlite3
import time

_ID = re.compile(r'[A-Za-z0-9_-]{8,96}\Z')


class DurableWorkerJobs:
    def __init__(self, path, *, clock=time.time):
        self.path = str(path)
        self.clock = clock
        with self._db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS worker_job_safety (
                worker_id TEXT PRIMARY KEY, job_id TEXT, state TEXT NOT NULL
                CHECK(state IN ('running','idle_confirmed','unknown')),
                deadline REAL, updated_at REAL NOT NULL)''')

    def _db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.execute('PRAGMA busy_timeout=10000')
        return db

    @staticmethod
    def _id(value):
        if not isinstance(value, str) or not _ID.fullmatch(value):
            raise ValueError('Invalid identifier')

    def assign(self, worker_id, job_id, *, timeout_seconds):
        """Called only by coordinator after assignment is durably accepted."""
        self._id(worker_id)
        self._id(job_id)
        if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 86400:
            raise ValueError('Invalid timeout')
        now = self.clock()
        with self._db() as db:
            # Serialize read-check-write across independent coordinator processes.
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT state FROM worker_job_safety WHERE worker_id=?', (worker_id,)).fetchone()
            if row and row[0] != 'idle_confirmed':
                raise ValueError('Worker not confirmed idle')
            db.execute('''INSERT INTO worker_job_safety VALUES (?,?,'running',?,?)
                ON CONFLICT(worker_id) DO UPDATE SET job_id=excluded.job_id,
                state='running',deadline=excluded.deadline,updated_at=excluded.updated_at''',
                (worker_id, job_id, now + timeout_seconds, now))

    def complete(self, worker_id, job_id):
        """Only after coordinator validates and commits terminal result."""
        self._id(worker_id)
        self._id(job_id)
        now = self.clock()
        with self._db() as db:
            result = db.execute('''UPDATE worker_job_safety SET job_id=NULL,
                state='idle_confirmed',deadline=NULL,updated_at=?
                WHERE worker_id=? AND job_id=? AND state='running' AND deadline>?''',
                (now, worker_id, job_id, now))
            if result.rowcount != 1:
                raise ValueError('Missing, expired or non-running job')

    def state(self, worker_id):
        self._id(worker_id)
        with self._db() as db:
            row = db.execute('SELECT state,deadline FROM worker_job_safety WHERE worker_id=?', (worker_id,)).fetchone()
        if not row:
            return 'unknown'
        if row[0] == 'running' and (row[1] is None or row[1] <= self.clock()):
            return 'unknown'
        return row[0]

    def update_safety_snapshot(self, worker_id, *, max_idle_age_seconds=30):
        """Coordinator-owned evidence: stale terminal state must not imply idle.

        Return a snapshot only. This cannot reserve idle, block job assignment,
        or authorize installation. A new assignment may begin immediately.
        """
        self._id(worker_id)
        if type(max_idle_age_seconds) not in (int, float) or not 0 < max_idle_age_seconds <= 30:
            raise ValueError('Invalid idle evidence lifetime')
        with self._db() as db:
            row = db.execute(
                'SELECT state,deadline,updated_at FROM worker_job_safety WHERE worker_id=?',
                (worker_id,)
            ).fetchone()
        now = self.clock()
        if not row:
            return 'unknown', None
        state, deadline, updated_at = row
        if state == 'running':
            return ('running', 1) if deadline is not None and deadline > now else ('unknown', None)
        # Only a recently committed terminal result can prove idle. Never
        # refresh an old DB row merely because a read-only API was called.
        if state == 'idle_confirmed' and updated_at is not None and (
            0 <= now - updated_at <= max_idle_age_seconds
        ):
            return 'idle_confirmed', 0
        return 'unknown', None

    def recover_after_restart(self):
        """Invalidate every persisted idle/running claim on coordinator boot.

        Call before accepting new worker traffic. A fresh coordinator must
        explicitly reconcile jobs before any worker can be declared idle.
        """
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("""UPDATE worker_job_safety SET state='unknown',
                job_id=NULL, deadline=NULL, updated_at=?""", (self.clock(),))

    def invalidate(self, worker_id):
        """Coordinator restart/revocation/uncertain state: never infer idle."""
        self._id(worker_id)
        with self._db() as db:
            db.execute('''INSERT INTO worker_job_safety VALUES (?,NULL,'unknown',NULL,?)
                ON CONFLICT(worker_id) DO UPDATE SET job_id=NULL,state='unknown',
                deadline=NULL,updated_at=excluded.updated_at''', (worker_id, self.clock()))
