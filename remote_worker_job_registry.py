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
        # The canonical registry accepts integer lease durations only.
        # Round down to avoid extending the requested deadline.
        import math
        if not math.isfinite(remaining) or not 1 <= remaining <= 86400:
            raise ValueError('Invalid deadline')
        self.assign(worker_id, job_id, timeout_seconds=int(remaining))

    def record_terminal(self, worker_id: str, job_id: str):
        self.complete(worker_id, job_id)
