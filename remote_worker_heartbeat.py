"""Authenticated worker presence signal; not proof of job execution capacity.

Heartbeat records are observational only: they never mark workers idle or
reconcile unknown jobs after a coordinator restart.
"""
import sqlite3
import time

from remote_worker_tokens import WorkerTokenStore


class WorkerHeartbeatStore:
    def __init__(self, db_path, *, clock=time.time):
        self.path = str(db_path)
        self.clock = clock
        self.tokens = WorkerTokenStore(db_path, clock=clock)
        with self._db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS worker_heartbeats (
                worker_id TEXT PRIMARY KEY, observed_at REAL NOT NULL)''')

    def _db(self):
        return sqlite3.connect(self.path, timeout=10)

    def record(self, worker_id, token):
        if not self.tokens.authenticate(worker_id, token):
            raise ValueError('Invalid worker credentials')
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            # Authentication is rechecked while holding the write transaction
            # so a concurrent token revocation cannot be bypassed.
            import hmac
            row = db.execute('SELECT token_digest,expires_at,revoked_at FROM worker_tokens WHERE worker_id=?',
                             (worker_id,)).fetchone()
            if not row or row[2] is not None or row[1] <= self.clock() or not hmac.compare_digest(row[0], self.tokens._digest(token)):
                raise ValueError('Credentials revoked')
            db.execute('''INSERT INTO worker_heartbeats VALUES (?,?)
                ON CONFLICT(worker_id) DO UPDATE SET observed_at=excluded.observed_at''',
                (worker_id, self.clock()))

    def recent(self, worker_id, *, max_age=30):
        WorkerTokenStore._worker(worker_id)
        if not 0 < max_age <= 300:
            raise ValueError('Invalid heartbeat age')
        with self._db() as db:
            row = db.execute('SELECT observed_at FROM worker_heartbeats WHERE worker_id=?',
                             (worker_id,)).fetchone()
        now = self.clock()
        return bool(row and 0 <= now - row[0] <= max_age)
