"""Coordinator-authoritative, durable per-worker task statistics.

Record events only after coordinator validates a job transition. Never trust
worker-supplied counts, durations or GPU utilization as authoritative.
"""
from __future__ import annotations

import re
import sqlite3
import time
from pathlib import Path

_WORKER = re.compile(r'[A-Za-z0-9_-]{8,96}\Z')
_STATUSES = {'success', 'error', 'cancelled'}


class WorkerMetrics:
    def __init__(self, path: str | Path, *, clock=time.time):
        self.path = str(path)
        self.clock = clock
        with self._db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS worker_task_metrics (
                job_id TEXT PRIMARY KEY, worker_id TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('success','error','cancelled')),
                duration_ms INTEGER NOT NULL CHECK(duration_ms >= 0),
                moves INTEGER NOT NULL CHECK(moves >= 0),
                cpu_ms INTEGER NOT NULL CHECK(cpu_ms >= 0),
                gpu_ms INTEGER NOT NULL CHECK(gpu_ms >= 0),
                finished_at INTEGER NOT NULL)''')
            db.execute('CREATE INDEX IF NOT EXISTS idx_worker_metrics_worker_time ON worker_task_metrics(worker_id,finished_at)')

    def _db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.execute('PRAGMA busy_timeout=10000')
        return db

    def record(self, *, job_id: str, worker_id: str, status: str,
               duration_ms: int, moves: int = 0, cpu_ms: int = 0,
               gpu_ms: int = 0, finished_at: int | None = None) -> bool:
        """Coordinator-only: return False on duplicate job, never double-count."""
        if not isinstance(job_id, str) or not _WORKER.fullmatch(job_id):
            raise ValueError('Invalid job ID')
        if not isinstance(worker_id, str) or not _WORKER.fullmatch(worker_id):
            raise ValueError('Invalid worker ID')
        if status not in _STATUSES:
            raise ValueError('Invalid status')
        for number in (duration_ms, moves, cpu_ms, gpu_ms):
            if type(number) is not int or number < 0 or number > 10**12:
                raise ValueError('Invalid metric')
        if finished_at is None:
            finished_at = int(self.clock())
        if type(finished_at) is not int or finished_at < 0:
            raise ValueError('Invalid timestamp')
        with self._db() as db:
            result = db.execute('''INSERT OR IGNORE INTO worker_task_metrics
                (job_id,worker_id,status,duration_ms,moves,cpu_ms,gpu_ms,finished_at)
                VALUES (?,?,?,?,?,?,?,?)''',
                (job_id, worker_id, status, duration_ms, moves, cpu_ms, gpu_ms, finished_at))
            return result.rowcount == 1

    def summary(self, *, since: int | None = None) -> list[dict]:
        """No date filter means all-time history, including revoked tokens."""
        if since is not None and (type(since) is not int or since < 0):
            raise ValueError('Invalid time filter')
        with self._db() as db:
            db.row_factory = sqlite3.Row
            rows = db.execute('''SELECT worker_id, COUNT(*) AS tasks,
                SUM(status='success') AS successes,
                SUM(status='error') AS errors,
                SUM(status='cancelled') AS cancelled,
                SUM(duration_ms) AS duration_ms, SUM(moves) AS moves,
                SUM(cpu_ms) AS cpu_ms, SUM(gpu_ms) AS gpu_ms,
                MAX(finished_at) AS last_activity
                FROM worker_task_metrics WHERE (? IS NULL OR finished_at >= ?)
                GROUP BY worker_id ORDER BY worker_id''', (since, since)).fetchall()
        return [dict(row) for row in rows]
