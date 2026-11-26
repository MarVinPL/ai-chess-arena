"""Read-only delivery of a coordinator-authorized, fenced resume checkpoint.
Delivery never attests engine acceptance or grants an execution lease.
"""
import base64
import hashlib
import secrets
import time

from remote_worker_update_pause import WorkerUpdatePause

MAX_CHECKPOINT_BYTES = 1024 * 1024


class ResumeTransport:
    def __init__(self, path, *, clock=time.time):
        self.pause = WorkerUpdatePause(path, clock=clock)
        self.clock = clock
        with self.pause.jobs._db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS worker_resume_delivery (
                worker_id TEXT PRIMARY KEY, job_id TEXT NOT NULL,
                request_id TEXT NOT NULL, digest TEXT NOT NULL,
                stop_digest TEXT NOT NULL, expires_at REAL NOT NULL)''')

    def _snapshot(self, db, worker, job, request, digest):
        self.pause.jobs._reject_update_hold(db, worker)
        self.pause.jobs._reject_model_maintenance_hold(db, worker)
        row = db.execute('SELECT job_id,request_id,checkpoint_json,checkpoint_sha256 FROM worker_update_resume WHERE worker_id=?', (worker,)).fetchone()
        state = db.execute('SELECT job_id,state FROM worker_job_safety WHERE worker_id=?', (worker,)).fetchone()
        pause = db.execute('SELECT job_id,request_id,phase,stop_lease_sha256 FROM worker_update_pause_requests WHERE worker_id=?', (worker,)).fetchone()
        if not row or row[:2] != (job, request) or row[3] != digest or state != (None, 'unknown') or not pause or pause[:3] != (job, request, 'acknowledged'):
            raise ValueError('Resume state mismatch')
        stop = self.pause._stop_lease_digest(db, worker, job)
        report = db.execute('SELECT job_id,request_id,lease_sha256 FROM worker_native_stop_reports WHERE worker_id=?', (worker,)).fetchone()
        if not stop or pause[3] != stop or report != (job, request, stop):
            raise ValueError('Missing fenced STOP evidence')
        raw = row[2].encode('utf-8')
        if not 0 < len(raw) <= MAX_CHECKPOINT_BYTES or not secrets.compare_digest(hashlib.sha256(raw).hexdigest(), digest):
            raise ValueError('Invalid checkpoint bytes')
        return raw, stop

    def authorize(self, worker, *, installed_identity_verified, stopped_engine_verified, checkpoint_verifier, ttl_seconds=300):
        """Private coordinator entrypoint; never accept these attestations over HTTP."""
        if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 600:
            raise ValueError('Invalid delivery lifetime')
        offer = self.pause.prepare_trusted_resume_offer(worker, installed_identity_verified=installed_identity_verified, stopped_engine_verified=stopped_engine_verified, checkpoint_verifier=checkpoint_verifier)
        job, request, digest = (offer[k] for k in ('job_id', 'request_id', 'checkpoint_sha256'))
        with self.pause.jobs._db() as db:
            db.execute('BEGIN IMMEDIATE')
            _, stop = self._snapshot(db, worker, job, request, digest)
            db.execute('INSERT INTO worker_resume_delivery VALUES (?,?,?,?,?,?) ON CONFLICT(worker_id) DO UPDATE SET job_id=excluded.job_id,request_id=excluded.request_id,digest=excluded.digest,stop_digest=excluded.stop_digest,expires_at=excluded.expires_at', (worker, job, request, digest, stop, self.clock()+ttl_seconds))

    def discover(self, worker):
        """Only expose identifiers for a currently authorized delivery."""
        self.pause.jobs._id(worker)
        with self.pause.jobs._db() as db:
            db.execute('BEGIN')
            offer = db.execute('SELECT job_id,request_id,digest,stop_digest,expires_at FROM worker_resume_delivery WHERE worker_id=?', (worker,)).fetchone()
            if not offer or offer[4] <= self.clock():
                return None
            try:
                _, stop = self._snapshot(db, worker, *offer[:3])
            except ValueError:
                return None
            if stop != offer[3]:
                return None
            return dict(version=1, job_id=offer[0], request_id=offer[1], checkpoint_sha256=offer[2])

    def fetch(self, worker, job, request, digest):
        """Authenticated caller only. Snapshot validation; no resume state mutation."""
        self.pause.jobs._id(worker)
        self.pause.jobs._id(job)
        with self.pause.jobs._db() as db:
            db.execute('BEGIN')
            offer = db.execute('SELECT job_id,request_id,digest,stop_digest,expires_at FROM worker_resume_delivery WHERE worker_id=?', (worker,)).fetchone()
            if not offer or offer[:3] != (job, request, digest) or offer[4] <= self.clock():
                raise ValueError('No valid delivery authorization')
            raw, stop = self._snapshot(db, worker, job, request, digest)
            if stop != offer[3]:
                raise ValueError('STOP lease changed')
            return dict(version=1, state='checkpoint_delivered', job_id=job, request_id=request, checkpoint_sha256=digest, checkpoint_size=len(raw), checkpoint_base64=base64.b64encode(raw).decode('ascii'), resume_available=False)
