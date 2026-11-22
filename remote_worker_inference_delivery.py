"""Coordinator-owned assignment delivery for an Android chess inference Worker.

Arena retains sole ownership of chess positions, legal-move validation and
checkpoints. This module gives an authenticated worker a read-only copy of a
specifically assigned inference request; it never runs an LLM or moves pieces.
"""
import json
import sqlite3
import time

from remote_worker_jobs import DurableWorkerJobs
from remote_worker_protocol import validate_job

MAX_JOB_BYTES = 16_384


class WorkerInferenceDelivery:
    def __init__(self, path, *, clock=time.time):
        self.jobs = DurableWorkerJobs(path, clock=clock)
        self.path = str(path)
        self.clock = clock
        with self.jobs._db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS worker_inference_assignments (
                worker_id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL UNIQUE,
                job_json TEXT NOT NULL,
                created_at REAL NOT NULL
            )""")

    def assign_from_arena(self, worker_id, job):
        """Trusted Arena-only transaction; NOT exposed as a worker HTTP method.

        Insert the payload and execution lease in ONE SQLite transaction,
        guarded by all of the existing update/pause/resume barriers.
        """
        self.jobs._id(worker_id)
        validated = validate_job(job)
        encoded = json.dumps(validated, separators=(",", ":"), sort_keys=True,
                             ensure_ascii=True, allow_nan=False)
        if len(encoded.encode("utf-8")) > MAX_JOB_BYTES:
            raise ValueError("Inference job is too large")
        now = self.clock()
        timeout = validated["timeout_seconds"]
        with self.jobs._db() as db:
            db.execute("BEGIN IMMEDIATE")
            self.jobs._reject_update_hold(db, worker_id)
            self.jobs._reject_pending_pause(db, worker_id)
            self.jobs._reject_pending_resume(db, worker_id)
            existing = db.execute(
                "SELECT state,job_id FROM worker_job_safety WHERE worker_id=?",
                (worker_id,),
            ).fetchone()
            if not existing or existing != ("idle_confirmed", None):
                raise ValueError("Worker must first be confirmed idle")
            if db.execute(
                "SELECT 1 FROM worker_inference_assignments WHERE worker_id=?",
                (worker_id,),
            ).fetchone():
                raise ValueError("Previous inference assignment remains unresolved")
            db.execute(
                "INSERT INTO worker_inference_assignments VALUES (?,?,?,?)",
                (worker_id, validated["job_id"], encoded, now),
            )
            result = db.execute(
                """UPDATE worker_job_safety
                SET job_id=?,state='running',deadline=?,updated_at=?
                WHERE worker_id=? AND job_id IS NULL AND state='idle_confirmed'""",
                (validated["job_id"], now+timeout, now, worker_id),
            )
            if result.rowcount != 1:
                raise ValueError("Concurrent worker assignment changed")
            # Any exception rolls back BOTH payload and running lease.
        return validated["job_id"]

    def poll_assigned_job(self, worker_id):
        """Read-only. Never starts, retries, extends or revives an expired job."""
        self.jobs._id(worker_id)
        now = self.clock()
        with self.jobs._db() as db:
            db.execute("BEGIN")
            row = db.execute(
                "SELECT job_id,job_json FROM worker_inference_assignments WHERE worker_id=?",
                (worker_id,),
            ).fetchone()
            lease = db.execute(
                "SELECT job_id,state,deadline FROM worker_job_safety WHERE worker_id=?",
                (worker_id,),
            ).fetchone()
            barriers = any(db.execute(
                f"SELECT 1 FROM {table} WHERE worker_id=?", (worker_id,),
            ).fetchone() for table in (
                "worker_update_holds", "worker_update_pause_requests",
                "worker_update_resume"
            ))
        if barriers:
            return {"state": "blocked"}
        if row is None:
            return {"state": "none"}
        if not lease or lease[0] != row[0] or lease[1] != "running":
            return {"state": "blocked"}
        if not isinstance(lease[2], (int, float)) or lease[2] <= now:
            return {"state": "expired"}
        if len(row[1].encode("utf-8")) > MAX_JOB_BYTES:
            return {"state": "blocked"}
        try:
            payload = validate_job(json.loads(row[1]))
        except (TypeError, ValueError):
            return {"state": "blocked"}
        if payload["job_id"] != row[0]:
            return {"state": "blocked"}
        return {"state": "assigned", "job": payload}
