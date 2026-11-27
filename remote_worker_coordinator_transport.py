"""Coordinator-side wait for an already authenticated, staged worker result.

No network dispatch, no checkpoint writes, and no worker-idle inference.
Use only with a coordinator-owned job assignment and a single-writer runner.
"""
import time

from remote_worker_job_binding import BoundWorkerResults


class RemoteResultTimeout(TimeoutError):
    pass


class StagedResultTransport:
    def __init__(self, db_path, worker_id, *, poll_interval=0.05, clock=time.monotonic):
        if not 0 < poll_interval <= 1:
            raise ValueError('Invalid poll interval')
        self.jobs = BoundWorkerResults(db_path)
        self.worker_id = worker_id
        self.poll_interval = poll_interval
        self.clock = clock

    def wait(self, job, board, player, *, stop_event=None, timeout=600):
        if not 0 < timeout <= 600:
            raise ValueError('Invalid timeout')
        deadline = self.clock() + timeout
        while True:
            for job_id, worker_id, result in self.jobs.pending():
                if job_id == job['job_id'] and worker_id == self.worker_id:
                    return result
            if stop_event is not None and stop_event.is_set():
                raise InterruptedError('Remote job interrupted; reservation retained')
            if self.clock() >= deadline:
                raise RemoteResultTimeout('Remote result deadline exceeded; reservation retained')
            time.sleep(min(self.poll_interval, max(0, deadline - self.clock())))
