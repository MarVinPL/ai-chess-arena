"""Compatibility facade for the single durable worker safety registry.

The legacy remote_worker_job_state table is not authoritative. Old rows are
never imported as idle: a coordinator restart must reconcile from scratch.
"""
from __future__ import annotations

import time
from remote_worker_jobs import DurableWorkerJobs


class WorkerJobRegistry(DurableWorkerJobs):
    def __init__(self, path, *, clock=time.time):
        super().__init__(path, clock=clock)

    def _connect(self):
        return self._db()

    def record_assignment(self, worker_id: str, job_id: str, *, deadline: float):
        if not isinstance(deadline, (int, float)) or isinstance(deadline, bool):
            raise ValueError('Invalid deadline')
        remaining = deadline - self.clock()
        import math
        if not math.isfinite(remaining) or not 0 < remaining <= 86400:
            raise ValueError('Invalid deadline')
        # Preserve the precise absolute deadline; rounding lease durations
        # could reject short valid leases or extend them past expiry.
        self._validate_deadline_assignment(worker_id, job_id, deadline)

    def _validate_deadline_assignment(self, worker_id, job_id, deadline):
        self._id(worker_id)
        self._id(job_id)
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            self._reject_update_hold(db, worker_id)
            row = db.execute('SELECT state FROM worker_job_safety WHERE worker_id=?', (worker_id,)).fetchone()
            if row and row[0] != 'idle_confirmed':
                raise ValueError('Worker has unresolved job state')
            now = self.clock()
            if deadline <= now:
                raise ValueError('Expired deadline')
            db.execute("""INSERT INTO worker_job_safety VALUES (?,?,'running',?,?)
                ON CONFLICT(worker_id) DO UPDATE SET job_id=excluded.job_id,
                state='running',deadline=excluded.deadline,updated_at=excluded.updated_at""",
                (worker_id, job_id, deadline, now))

    def record_terminal(self, worker_id: str, job_id: str):
        self.complete(worker_id, job_id)
