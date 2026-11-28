"""Fail-closed coordinator-side eligibility check, not a live heartbeat.

An idle claim in SQLite is necessary but insufficient for actual dispatch:
the caller must additionally verify current worker liveness out-of-band.
"""
from remote_worker_job_binding import BoundWorkerResults
from remote_worker_tokens import WorkerTokenStore


class WorkerNotReady(RuntimeError):
    pass


def assert_worker_eligible(db_path, worker_id, *, liveness_verified=False):
    if liveness_verified is not True:
        raise WorkerNotReady('Fresh worker liveness verification required')
    WorkerTokenStore._worker(worker_id)
    jobs = BoundWorkerResults(db_path)
    with jobs._db() as db:
        db.execute('BEGIN IMMEDIATE')
        if jobs._journal_init_fenced(db, worker_id):
            raise WorkerNotReady('Worker journal initialization fenced')
        state = db.execute('SELECT state,job_id,deadline FROM worker_job_safety WHERE worker_id=?',
                           (worker_id,)).fetchone()
        if not state or state != ('idle_confirmed', None, None):
            raise WorkerNotReady('Worker is not durably idle')
        pending = db.execute("SELECT 1 FROM worker_result_journal WHERE worker_id=? AND state='pending_application' LIMIT 1",
                             (worker_id,)).fetchone()
        if pending:
            raise WorkerNotReady('Worker has unapplied result')
        token = db.execute('SELECT expires_at,revoked_at FROM worker_tokens WHERE worker_id=?',
                           (worker_id,)).fetchone()
        if not token or token[1] is not None or token[0] <= jobs.clock():
            raise WorkerNotReady('Worker credential not active')
    return True


def assert_worker_heartbeat_ready(db_path, worker_id, *, max_age=30):
    """Require recent authenticated presence AND durable idle state.

    Presence is observational, not capacity attestation. The database checks
    below run in one read transaction to avoid inconsistent safety snapshots.
    """
    from remote_worker_heartbeat import WorkerHeartbeatStore
    heartbeat = WorkerHeartbeatStore(db_path)
    if not heartbeat.recent(worker_id, max_age=max_age):
        raise WorkerNotReady('Fresh authenticated worker heartbeat required')
    return assert_worker_eligible(db_path, worker_id, liveness_verified=True)
