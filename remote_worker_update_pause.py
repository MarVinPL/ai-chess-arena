"""Coordinator-owned durable pause requests for Android Worker updates.

This module does NOT run or stop remote inference. A request is only an
instruction pending delivery, never proof that the Worker stopped. The
existing scheduler must send the request and obtain a verified checkpoint
ACK from a real worker transport before it can enter the installer path.
"""
import secrets
import sqlite3
import time

from remote_worker_jobs import DurableWorkerJobs


class WorkerUpdatePause:
    def __init__(self, path, *, clock=time.time):
        self.jobs = DurableWorkerJobs(path, clock=clock)
        self.path = str(path)
        self.clock = clock
        with self.jobs._db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS worker_update_pause_requests (
                worker_id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL,
                request_id TEXT NOT NULL UNIQUE,
                phase TEXT NOT NULL CHECK(phase IN ('requested','acknowledged')),
                created_at REAL NOT NULL,
                acknowledged_at REAL
            )""")

    def request(self, worker_id, job_id):
        """Durably queue a pause; do not mutate the running job or fake ACK."""
        self.jobs._id(worker_id)
        self.jobs._id(job_id)
        request_id = secrets.token_hex(16)
        with self.jobs._db() as db:
            db.execute("BEGIN IMMEDIATE")
            self.jobs._reject_update_hold(db, worker_id)
            row = db.execute(
                "SELECT job_id,state,deadline FROM worker_job_safety WHERE worker_id=?",
                (worker_id,),
            ).fetchone()
            if not row or row[0] != job_id or row[1] != "running" or (
                row[2] is None or row[2] <= self.clock()
            ):
                raise ValueError("No active verified job to pause")
            if db.execute(
                "SELECT 1 FROM worker_update_pause_requests WHERE worker_id=?",
                (worker_id,),
            ).fetchone():
                raise ValueError("Pause is already requested")
            db.execute(
                """INSERT INTO worker_update_pause_requests
                (worker_id,job_id,request_id,phase,created_at,acknowledged_at)
                VALUES (?,?,?,'requested',?,NULL)""",
                (worker_id, job_id, request_id, self.clock()),
            )
        return request_id

    def pending(self, worker_id):
        """Read a pending instruction; caller must deliver it over worker transport."""
        self.jobs._id(worker_id)
        with self.jobs._db() as db:
            row = db.execute(
                """SELECT job_id,request_id,phase FROM worker_update_pause_requests
                WHERE worker_id=?""",
                (worker_id,),
            ).fetchone()
        return None if row is None else {
            "job_id": row[0], "request_id": row[1], "phase": row[2]
        }

    def acknowledge(self, worker_id, job_id, request_id, *,
                    worker_stopped, checkpoint_verified):
        """Only coordinator-validated worker ACK+checkpoint can mark pause.

        This is a bookkeeping marker, NOT a scheduler idle transition:
        the running job remains running until the real worker's execution
        lease and saved checkpoint are integrated with atomic assignment.
        """
        self.jobs._id(worker_id)
        self.jobs._id(job_id)
        if not isinstance(request_id, str) or len(request_id) != 32 or (
            not all(ch in "0123456789abcdef" for ch in request_id)
        ):
            raise ValueError("Invalid pause request")
        if worker_stopped is not True or checkpoint_verified is not True:
            raise ValueError("Worker has not stopped with verified checkpoint")
        with self.jobs._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                """SELECT job_id,request_id,phase
                FROM worker_update_pause_requests WHERE worker_id=?""",
                (worker_id,),
            ).fetchone()
            active = db.execute(
                """SELECT job_id,state FROM worker_job_safety WHERE worker_id=?""",
                (worker_id,),
            ).fetchone()
            if row != (job_id, request_id, "requested") or active != (job_id, "running"):
                raise ValueError("Stale pause acknowledgment")
            db.execute(
                """UPDATE worker_update_pause_requests
                SET phase='acknowledged',acknowledged_at=?
                WHERE worker_id=?""",
                (self.clock(), worker_id),
            )

    def clear(self, worker_id, request_id):
        """Explicit cleanup after completed reconciliation; not automatic resume."""
        self.jobs._id(worker_id)
        with self.jobs._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT request_id FROM worker_update_pause_requests WHERE worker_id=?",
                (worker_id,),
            ).fetchone()
            if not row or not secrets.compare_digest(row[0], request_id):
                raise ValueError("Pause request mismatch")
            db.execute(
                "DELETE FROM worker_update_pause_requests WHERE worker_id=?", (worker_id,)
            )
