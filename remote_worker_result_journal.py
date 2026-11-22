"""Coordinator-owned crash-safe result staging; not a scheduler integration.

A validated result is staged exactly once. The caller must separately apply it
idempotently to the chess scheduler; this module does not claim that step.
"""
import json
import sqlite3
import time
from remote_worker_jobs import DurableWorkerJobs


class WorkerResultJournal(DurableWorkerJobs):
    def __init__(self, path, *, clock=time.time):
        super().__init__(path, clock=clock)
        with self._db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS worker_result_journal (
                job_id TEXT PRIMARY KEY, worker_id TEXT NOT NULL,
                result_json TEXT NOT NULL, state TEXT NOT NULL
                CHECK(state='pending_application'), created_at REAL NOT NULL)''')

    def stage_validated(self, worker_id, job_id, result):
        """Only call after validating result against the assigned board/job.

        The safety transition and replayable result commit are one transaction.
        """
        self._id(worker_id)
        self._id(job_id)
        if not isinstance(result, dict) or result.get('job_id') != job_id:
            raise ValueError('Invalid validated result')
        payload = json.dumps(result, sort_keys=True, separators=(',', ':'), allow_nan=False)
        if len(payload) > 16384:
            raise ValueError('Result too large')
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            previous = db.execute('SELECT worker_id, result_json FROM worker_result_journal WHERE job_id=?', (job_id,)).fetchone()
            if previous:
                if previous != (worker_id, payload):
                    raise ValueError('Conflicting result for job')
                return False
            now = self.clock()
            updated = db.execute('''UPDATE worker_job_safety SET job_id=NULL,
                state='idle_confirmed',deadline=NULL,updated_at=?
                WHERE worker_id=? AND job_id=? AND state='running' AND deadline>?''',
                (now, worker_id, job_id, now))
            if updated.rowcount != 1:
                raise ValueError('Missing, expired or non-running job')
            db.execute('''INSERT INTO worker_result_journal VALUES (?, ?, ?, 'pending_application', ?)''',
                       (job_id, worker_id, payload, now))
            return True

    def pending(self):
        with self._db() as db:
            rows = db.execute('''SELECT job_id,worker_id,result_json FROM worker_result_journal
                WHERE state='pending_application' ORDER BY created_at,job_id''').fetchall()
        return [(job, worker, json.loads(payload)) for job, worker, payload in rows]
