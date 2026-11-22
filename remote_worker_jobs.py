"""Durable coordinator-owned update safety state; no worker-side writes.

Only coordinator-validated transitions may modify this registry. Missing rows,
expired leases and interrupted tasks fail closed. This is not a job scheduler.
"""
import re
import secrets
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

            db.execute('''CREATE TABLE IF NOT EXISTS worker_update_holds (
                worker_id TEXT PRIMARY KEY, hold_token TEXT NOT NULL,
                created_at REAL NOT NULL, expires_at REAL NOT NULL)''')

            # Shared coordinator lock. Requests must block a racing new
            # assignment before a trusted STOP/checkpoint ACK is delivered.
            db.execute('''CREATE TABLE IF NOT EXISTS worker_update_pause_requests (
                worker_id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL,
                request_id TEXT NOT NULL UNIQUE,
                phase TEXT NOT NULL CHECK(phase IN ('requested','acknowledged')),
                created_at REAL NOT NULL,
                acknowledged_at REAL
            )''')

    def _db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.execute('PRAGMA busy_timeout=10000')
        return db

    @staticmethod
    def _id(value):
        if not isinstance(value, str) or not _ID.fullmatch(value):
            raise ValueError('Invalid identifier')

    def _reject_pending_pause(self, db, worker_id):
        # A requested or ACKed pause is not idle. Block assignment even if
        # another coordinator thread commits the last job concurrently.
        if db.execute(
            'SELECT 1 FROM worker_update_pause_requests WHERE worker_id=?',
            (worker_id,)
        ).fetchone():
            raise ValueError('Worker pause for update pending; assignment blocked')

    def _reject_update_hold(self, db, worker_id):
        # HOLD IS DURABLE AND FAIL-CLOSED: even an expired hold continues
        # blocking assignments until explicit coordinator reconciliation.
        # An expiry alone never proves that Android's installer has finished.
        if db.execute('SELECT 1 FROM worker_update_holds WHERE worker_id=?',
                      (worker_id,)).fetchone():
            raise ValueError('Worker reserved for update; assignment blocked')

    def reserve_update(self, worker_id, *, ttl_seconds=300, hold_token=None):
        """Atomically reserve a recently confirmed idle worker for update.

        Only a coordinator-controlled call may invoke this method. The token
        is a random capability; never expose it to worker listing or logs.
        Expiry limits its validity for installation, not the durable block.
        """
        self._id(worker_id)
        if type(ttl_seconds) is not int or not 30 <= ttl_seconds <= 900:
            raise ValueError('Invalid update hold TTL')
        now = self.clock()
        # Android journals its CSPRNG-generated hold token securely BEFORE
        # calling the coordinator, so a lost response cannot orphan the hold.
        # Legacy trusted callers may still let the server generate the token.
        if hold_token is None:
            token = secrets.token_hex(32)
        elif (not isinstance(hold_token, str) or
              not re.fullmatch(r'[0-9a-f]{64}', hold_token)):
            raise ValueError('Invalid client-generated update hold token')
        else:
            token = hold_token
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            self._reject_update_hold(db, worker_id)
            self._reject_pending_pause(db, worker_id)
            row = db.execute('''SELECT state,job_id,deadline,updated_at
                                FROM worker_job_safety WHERE worker_id=?''',
                             (worker_id,)).fetchone()
            if not row or row[0] != 'idle_confirmed' or row[1] is not None or (
                row[2] is not None
            ) or not (0 <= now - row[3] <= 30):
                raise ValueError('Worker idle is not freshly confirmed')
            db.execute('''INSERT INTO worker_update_holds
                (worker_id,hold_token,created_at,expires_at) VALUES (?,?,?,?)''',
                (worker_id, token, now, now + ttl_seconds))
        return token

    def validate_update_hold(self, worker_id, token):
        """Return true only for currently usable reservation evidence."""
        self._id(worker_id)
        if not isinstance(token, str) or not re.fullmatch(r'[0-9a-f]{64}', token):
            return False
        with self._db() as db:
            row = db.execute('''SELECT hold_token,expires_at FROM worker_update_holds
                                WHERE worker_id=?''', (worker_id,)).fetchone()
        return bool(row and secrets.compare_digest(row[0], token)
                    and self.clock() < row[1])

    def inspect_update_hold(self, worker_id, token):
        """Read-only recovery evidence for the original, journaled token.

        'absent' does NOT prove that Android PackageInstaller has finished.
        'expired' remains a durable assignment block, not an auto-release.
        A different token is rejected without disclosing reservation details.
        """
        self._id(worker_id)
        if not isinstance(token, str) or not re.fullmatch(r'[0-9a-f]{64}', token):
            raise ValueError('Invalid update reservation capability')
        with self._db() as db:
            row = db.execute(
                'SELECT hold_token,expires_at FROM worker_update_holds WHERE worker_id=?',
                (worker_id,)
            ).fetchone()
        if row is None:
            return 'absent'
        if not secrets.compare_digest(row[0], token):
            raise ValueError('Update reservation mismatch')
        return 'active' if self.clock() < row[1] else 'expired'

    def release_update_hold(self, worker_id, token):
        """Explicit coordinator release. Only the exact token clears a hold.

        An expired reservation stays blocked; an authenticated release holding
        the original token can still recover it after expiry.
        """
        self._id(worker_id)
        if not isinstance(token, str) or not re.fullmatch(r'[0-9a-f]{64}', token):
            raise ValueError('Invalid update reservation')
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('''SELECT hold_token FROM worker_update_holds
                                WHERE worker_id=?''', (worker_id,)).fetchone()
            if not row or not secrets.compare_digest(row[0], token):
                raise ValueError('Wrong update reservation')
            db.execute('DELETE FROM worker_update_holds WHERE worker_id=?',
                       (worker_id,))
            # Releasing the hold is NOT proof that the app came back online.
            # Force reconciliation before this worker may accept new jobs.
            db.execute('''UPDATE worker_job_safety SET state='unknown',
                          job_id=NULL,deadline=NULL,updated_at=?
                          WHERE worker_id=?''', (self.clock(), worker_id))

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
            self._reject_update_hold(db, worker_id)
            self._reject_pending_pause(db, worker_id)
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
            hold = db.execute(
                'SELECT 1 FROM worker_update_holds WHERE worker_id=?',
                (worker_id,)
            ).fetchone()
        now = self.clock()
        # A reserved worker is not available for fresh idle authorization.
        if not row or hold:
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
