"""SQLite-backed worker credentials; transport and admin authorization are external.

Tokens are only returned once at issuance. Never expose this module directly as
an unauthenticated HTTP endpoint. SQLite file access must be restricted to VPS.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import sqlite3
import time
from pathlib import Path

_WORKER = re.compile(r'[A-Za-z0-9_-]{8,96}\Z')


class WorkerTokenError(ValueError):
    pass


class WorkerTokenStore:
    def __init__(self, path: str | Path, *, clock=time.time):
        self.path = str(path)
        self.clock = clock
        with self._connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS worker_tokens (
                worker_id TEXT PRIMARY KEY, token_digest BLOB NOT NULL,
                expires_at INTEGER NOT NULL, revoked_at INTEGER)''')

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.execute('PRAGMA busy_timeout=10000')
        return db

    @staticmethod
    def _digest(token: str) -> bytes:
        return hashlib.sha256(token.encode('ascii')).digest()

    @staticmethod
    def _worker(worker_id: str):
        if not isinstance(worker_id, str) or not _WORKER.fullmatch(worker_id):
            raise WorkerTokenError('Invalid worker ID')

    def issue(self, worker_id: str, *, ttl_seconds: int = 30 * 86400) -> str:
        """Admin-only operation: replaces prior token for this worker."""
        self._worker(worker_id)
        if type(ttl_seconds) is not int or not 60 <= ttl_seconds <= 90 * 86400:
            raise WorkerTokenError('Invalid token TTL')
        token = secrets.token_urlsafe(32)
        digest = self._digest(token)
        with self._connect() as db:
            db.execute('''INSERT INTO worker_tokens VALUES (?, ?, ?, NULL)
                ON CONFLICT(worker_id) DO UPDATE SET
                token_digest=excluded.token_digest,
                expires_at=excluded.expires_at, revoked_at=NULL''',
                (worker_id, digest, int(self.clock()) + ttl_seconds))
        return token

    def authenticate(self, worker_id: str, token: str) -> bool:
        with self._connect() as db:
            return self.authenticate_in_snapshot(db, worker_id, token)

    def authenticate_in_snapshot(self, db, worker_id: str, token: str) -> bool:
        """Authenticate against the caller's SQLite transaction."""
        if not isinstance(worker_id, str) or not _WORKER.fullmatch(worker_id):
            return False
        if not isinstance(token, str) or not 24 <= len(token) <= 512 or not token.isascii():
            return False
        row = db.execute('SELECT token_digest, expires_at, revoked_at FROM worker_tokens WHERE worker_id=?',
                         (worker_id,)).fetchone()
        return bool(row and row[2] is None and row[1] > self.clock()
                    and hmac.compare_digest(row[0], self._digest(token)))

    def revoke(self, worker_id: str) -> None:
        """Admin-only operation: immediately invalidates existing token."""
        self._worker(worker_id)
        with self._connect() as db:
            db.execute('UPDATE worker_tokens SET revoked_at=? WHERE worker_id=?',
                       (int(self.clock()), worker_id))
