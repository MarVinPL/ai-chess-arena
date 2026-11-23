"""Coordinator-owned durable pause requests for Android Worker updates.

This module does NOT run or stop remote inference. A request is only an
instruction pending delivery, never proof that the Worker stopped. The
existing scheduler must send the request and obtain a verified checkpoint
ACK from a real worker transport before it can enter the installer path.
"""
import hashlib
import json
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
            db.execute("""CREATE TABLE IF NOT EXISTS worker_update_resume (
                worker_id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL,
                request_id TEXT NOT NULL UNIQUE,
                checkpoint_json TEXT NOT NULL,
                checkpoint_sha256 TEXT NOT NULL,
                created_at REAL NOT NULL
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
            existing = db.execute(
                "SELECT job_id,request_id,phase FROM worker_update_pause_requests "
                "WHERE worker_id=?", (worker_id,),
            ).fetchone()
            if existing is not None:
                # Retry after a lost coordinator response: the same running
                # job reuses the durable STOP request, never forges an ACK.
                if existing[0] == job_id and existing[2] == "requested":
                    return existing[1]
                raise ValueError("Pause is already requested for another state")
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

    def poll_stop_instruction(self, worker_id):
        """Read-only instruction for a machine-authenticated Worker poll.

        A request is not proof of delivery or an ACK. Repeated polls may
        return the SAME request_id; the future executor must deduplicate it.
        Expired/mismatched execution state remains blocked, never idle.
        """
        self.jobs._id(worker_id)
        with self.jobs._db() as db:
            # One SQLite read snapshot prevents torn STOP and lease reads
            # during concurrent coordinator reconciliation. Polling stays
            # read-only and is never an acknowledgment of model termination.
            db.execute("BEGIN")
            pause = db.execute(
                "SELECT job_id,request_id,phase FROM worker_update_pause_requests "
                "WHERE worker_id=?", (worker_id,)
            ).fetchone()
            active = db.execute(
                "SELECT job_id,state,deadline FROM worker_job_safety "
                "WHERE worker_id=?", (worker_id,)
            ).fetchone()
            if pause is None:
                return {"action": "none"}
            job_id, request_id, phase = pause
            if phase == "acknowledged":
                return {"action": "await_reconciliation"}
            if phase != "requested" or not active or (
                active[0] != job_id or active[1] != "running" or
                active[2] is None or active[2] <= self.clock()
            ):
                return {"action": "blocked"}

            try:
                lease_digest = self._stop_lease_digest(db, worker_id, job_id)
            except ValueError:
                return {"action": "blocked"}
        instruction = {
            "action": "stop_checkpoint",
            "job_id": job_id, "request_id": request_id,
        }
        # Protocol v3: bind the STOP instruction to the exact execution
        # generation without echoing its 256-bit bearer capability. A legacy
        # v1/v2 job keeps the unchanged instruction format.
        if lease_digest is not None:
            instruction["lease_token_sha256"] = lease_digest
        return instruction

    @staticmethod
    def _stop_lease_digest(db, worker_id, job_id):
        """Read the execution lease in the caller's STOP transaction."""
        # Fresh and legacy registries may predate inference assignment storage.
        if not db.execute(
            "SELECT 1 FROM sqlite_schema "
            "WHERE type='table' AND name='worker_inference_assignments'"
        ).fetchone():
            return None
        assignment = db.execute(
            "SELECT job_id,job_json FROM worker_inference_assignments WHERE worker_id=?",
            (worker_id,),
        ).fetchone()
        if assignment is None:
            return None
        job = json.loads(assignment[1])
        if assignment[0] != job_id or not isinstance(job, dict) or job.get("job_id") != job_id:
            raise ValueError("Mismatched STOP assignment")
        version = job.get("version")
        if type(version) is not int or version not in (1, 2, 3):
            raise ValueError("Invalid STOP assignment protocol")
        if version != 3:
            return None
        token = job.get("lease_token")
        if not isinstance(token, str) or len(token) != 64 or any(
            c not in "0123456789abcdef" for c in token
        ):
            raise ValueError("Invalid STOP assignment lease")
        return hashlib.sha256(token.encode("ascii")).hexdigest()

    def acknowledge(self, worker_id, job_id, request_id, *,
                    worker_stopped, checkpoint_verified, lease_token_sha256=None):
        """Only coordinator-validated worker ACK+checkpoint can mark pause.

        This is a bookkeeping marker, NOT a scheduler idle transition:
        the running job remains running until the real worker's execution
        lease and saved checkpoint are integrated with atomic assignment.
        For v3, the trusted caller must match the stopped execution lease.
        A retry for that same live lease leaves the original ACK intact.
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
            self.jobs._reject_update_hold(db, worker_id)
            row = db.execute(
                """SELECT job_id,request_id,phase
                FROM worker_update_pause_requests WHERE worker_id=?""",
                (worker_id,),
            ).fetchone()
            active = db.execute(
                """SELECT job_id,state,deadline FROM worker_job_safety WHERE worker_id=?""",
                (worker_id,),
            ).fetchone()
            if (not row or row[:2] != (job_id, request_id)
                    or not active or active[:2] != (job_id, "running")
                    or active[2] is None or active[2] <= self.clock()):
                raise ValueError("Stale pause acknowledgment")
            lease_digest = self._stop_lease_digest(db, worker_id, job_id)
            if lease_digest is not None:
                if (not isinstance(lease_token_sha256, str)
                        or len(lease_token_sha256) != 64 or not lease_token_sha256.isascii()
                        or not secrets.compare_digest(lease_digest, lease_token_sha256)):
                    raise ValueError("STOP acknowledgment has a stale or missing lease")
                if row[2] == "acknowledged":
                    return
            elif lease_token_sha256 is not None:
                raise ValueError("STOP acknowledgment lease does not match the job protocol")
            if row[2] != "requested":
                raise ValueError("Stale pause acknowledgment")
            db.execute(
                """UPDATE worker_update_pause_requests
                SET phase='acknowledged',acknowledged_at=?
                WHERE worker_id=?""",
                (self.clock(), worker_id),
            )

    def finalize_verified_pause(self, worker_id, job_id, request_id, *,
                                hold_token, checkpoint, worker_stopped_verified,
                                checkpoint_verifier):
        """Atomically preserve resume state and reserve a stopped Worker.

        INTERNAL trusted coordinator API, not an HTTP worker endpoint.
        The execution transport must establish actual STOP and pass a
        trusted application-level checkpoint verifier. Digest integrity
        alone never authenticates a remote Worker or validates legal moves.
        """
        self.jobs._id(worker_id)
        self.jobs._id(job_id)
        if not isinstance(request_id, str) or not (
            len(request_id) == 32 and all(c in "0123456789abcdef" for c in request_id)
        ):
            raise ValueError("Invalid pause request ID")
        if not isinstance(hold_token, str) or not (
            len(hold_token) == 64 and all(c in "0123456789abcdef" for c in hold_token)
        ):
            raise ValueError("Invalid client-journaled update hold token")
        if worker_stopped_verified is not True:
            raise ValueError("Trusted STOP acknowledgment is required")
        if not isinstance(checkpoint, dict) or (
            checkpoint.get("schema_version") != 1 or
            checkpoint.get("job_id") != job_id or
            checkpoint.get("resume_mode") not in ("resume", "restart") or
            not isinstance(checkpoint.get("payload"), dict)
        ):
            raise ValueError("Invalid resume checkpoint")
        if not callable(checkpoint_verifier) or checkpoint_verifier(checkpoint) is not True:
            raise ValueError("Checkpoint not verified by coordinator")
        try:
            canonical = json.dumps(checkpoint, sort_keys=True, ensure_ascii=False,
                                   separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError) as error:
            raise ValueError("Checkpoint cannot be serialized safely") from error
        if not 0 < len(canonical.encode("utf-8")) <= 1024 * 1024:
            raise ValueError("Checkpoint exceeds safe limit")
        sha256 = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        now = self.clock()
        with self.jobs._db() as db:
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            self.jobs._reject_update_hold(db, worker_id)
            if db.execute(
                "SELECT 1 FROM worker_update_resume WHERE worker_id=?", (worker_id,)
            ).fetchone():
                raise ValueError("Existing resume checkpoint must be reconciled")
            pause = db.execute(
                "SELECT job_id,request_id,phase FROM worker_update_pause_requests "
                "WHERE worker_id=?", (worker_id,)
            ).fetchone()
            job = db.execute(
                "SELECT job_id,state,deadline FROM worker_job_safety WHERE worker_id=?",
                (worker_id,)
            ).fetchone()
            if pause != (job_id, request_id, "acknowledged") or not job or (
                job[0] != job_id or job[1] != "running" or
                job[2] is None or job[2] <= now
            ):
                raise ValueError("Pause ACK or execution lease no longer valid")
            db.execute(
                """INSERT INTO worker_update_resume
                (worker_id,job_id,request_id,checkpoint_json,checkpoint_sha256,created_at)
                VALUES (?,?,?,?,?,?)""",
                (worker_id, job_id, request_id, canonical, sha256, now)
            )
            db.execute(
                """INSERT INTO worker_update_holds
                (worker_id,hold_token,created_at,expires_at)
                VALUES (?,?,?,?)""",
                (worker_id, hold_token, now, now + 300)
            )
            db.execute(
                """UPDATE worker_job_safety
                SET state='unknown',job_id=NULL,deadline=NULL,updated_at=?
                WHERE worker_id=? AND state='running' AND job_id=?""",
                (now, worker_id, job_id)
            )
            persisted = db.execute(
                "SELECT checkpoint_json,checkpoint_sha256 FROM worker_update_resume "
                "WHERE worker_id=?", (worker_id,)
            ).fetchone()
            if persisted != (canonical, sha256) or (
                hashlib.sha256(persisted[0].encode("utf-8")).hexdigest() != persisted[1]
            ):
                raise RuntimeError("Durable resume checkpoint readback failed")
        return {"worker_id": worker_id, "job_id": job_id,
                "request_id": request_id, "sha256": sha256}

    def read_resume_checkpoint(self, worker_id):
        """Integrity-checked read for a future trusted resume dispatcher.

        Does not clear the hold, resume inference or expose a secret.
        """
        self.jobs._id(worker_id)
        with self.jobs._db() as db:
            row = db.execute(
                "SELECT job_id,request_id,checkpoint_json,checkpoint_sha256 "
                "FROM worker_update_resume WHERE worker_id=?", (worker_id,)
            ).fetchone()
        if row is None:
            return None
        job_id, request_id, raw, digest = row
        if hashlib.sha256(raw.encode("utf-8")).hexdigest() != digest:
            raise ValueError("Corrupted durable resume checkpoint")
        try:
            checkpoint = json.loads(raw)
        except (TypeError, json.JSONDecodeError) as error:
            raise ValueError("Malformed durable resume checkpoint") from error
        if not isinstance(checkpoint, dict) or checkpoint.get("job_id") != job_id or (
            checkpoint.get("schema_version") != 1 or
            checkpoint.get("resume_mode") not in ("resume", "restart")
        ):
            raise ValueError("Mismatched durable resume checkpoint")
        return {"job_id": job_id, "request_id": request_id,
                "checkpoint": checkpoint, "sha256": digest}

    def resume_status(self, worker_id):
        """Read-only coordinator status for the post-install Resume UI.

        Never exposes checkpoint payload, request ID or hold capability.
        A verified checkpoint without a dispatcher is NOT runnable; this
        remains blocked until actual inference acceptance is wired.
        """
        self.jobs._id(worker_id)
        saved = self.read_resume_checkpoint(worker_id)
        if saved is None:
            return {"state": "no_checkpoint", "resume_available": False}
        with self.jobs._db() as db:
            worker = db.execute(
                "SELECT state,job_id FROM worker_job_safety WHERE worker_id=?",
                (worker_id,),
            ).fetchone()
            hold = db.execute(
                "SELECT 1 FROM worker_update_holds WHERE worker_id=?",
                (worker_id,),
            ).fetchone()
            pause = db.execute(
                "SELECT phase FROM worker_update_pause_requests WHERE worker_id=?",
                (worker_id,),
            ).fetchone()
        if hold:
            state = "waiting_update_reconciliation"
        elif worker != ("unknown", None) or pause != ("acknowledged",):
            state = "blocked"
        else:
            state = "awaiting_local_engine"
        return {
            "state": state,
            "resume_available": False,
            "job_id": saved["job_id"],
            "checkpoint_sha256": saved["sha256"],
        }

    def reconcile_resume(self, worker_id, job_id, request_id, *,
                         checkpoint_sha256, timeout_seconds,
                         worker_ready_verified, resume_accepted_verified,
                         checkpoint_verifier):
        """Commit a trusted worker's acceptance of the interrupted job.

        No remote transport is implemented here. The authenticated dispatcher
        must check installed version/signature, send exactly this checkpoint
        to the same worker, and obtain a durable, idempotent acceptance ACK
        before passing trusted evidence. Merely being online is insufficient.
        This CAS transaction simultaneously restores the active job lease and
        removes both pause/resume gates. Replays cannot reassign twice.
        """
        self.jobs._id(worker_id)
        self.jobs._id(job_id)
        if not isinstance(request_id, str) or not (
            len(request_id) == 32 and all(c in "0123456789abcdef" for c in request_id)
        ):
            raise ValueError("Invalid pause request ID")
        if not isinstance(checkpoint_sha256, str) or not (
            len(checkpoint_sha256) == 64 and
            all(c in "0123456789abcdef" for c in checkpoint_sha256)
        ):
            raise ValueError("Invalid checkpoint digest")
        if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 86400:
            raise ValueError("Invalid resumed execution lease")
        if worker_ready_verified is not True or resume_accepted_verified is not True:
            raise ValueError("Trusted Worker resume acceptance required")
        if not callable(checkpoint_verifier):
            raise ValueError("Trusted checkpoint verifier missing")
        now = self.clock()
        with self.jobs._db() as db:
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            self.jobs._reject_update_hold(db, worker_id)
            row = db.execute(
                "SELECT job_id,request_id,checkpoint_json,checkpoint_sha256 "
                "FROM worker_update_resume WHERE worker_id=?", (worker_id,)
            ).fetchone()
            if row is None or row[:2] != (job_id, request_id) or (
                not secrets.compare_digest(row[3], checkpoint_sha256)
            ):
                raise ValueError("Resume checkpoint mismatch or already consumed")
            if hashlib.sha256(row[2].encode("utf-8")).hexdigest() != row[3]:
                raise ValueError("Corrupted resume checkpoint")
            try:
                checkpoint = json.loads(row[2])
            except (TypeError, json.JSONDecodeError) as error:
                raise ValueError("Invalid resume checkpoint JSON") from error
            if not isinstance(checkpoint, dict) or (
                checkpoint.get("schema_version") != 1 or
                checkpoint.get("job_id") != job_id or
                checkpoint.get("resume_mode") not in ("resume", "restart") or
                not isinstance(checkpoint.get("payload"), dict)
            ):
                raise ValueError("Wrong resume checkpoint identity")
            if checkpoint_verifier(checkpoint) is not True:
                raise ValueError("Coordinator rejected resume checkpoint")
            pause = db.execute(
                "SELECT job_id,request_id,phase "
                "FROM worker_update_pause_requests WHERE worker_id=?",
                (worker_id,)
            ).fetchone()
            worker = db.execute(
                "SELECT job_id,state FROM worker_job_safety WHERE worker_id=?",
                (worker_id,)
            ).fetchone()
            if pause != (job_id, request_id, "acknowledged") or worker != (None, "unknown"):
                raise ValueError("Worker state not reconciled after update")
            updated = db.execute(
                "UPDATE worker_job_safety SET job_id=?,state='running',"
                "deadline=?,updated_at=? "
                "WHERE worker_id=? AND job_id IS NULL AND state='unknown'",
                (job_id, now + timeout_seconds, now, worker_id)
            )
            if updated.rowcount != 1:
                raise ValueError("Worker execution lease was concurrently changed")
            db.execute(
                "DELETE FROM worker_update_resume WHERE worker_id=? "
                "AND job_id=? AND request_id=? AND checkpoint_sha256=?",
                (worker_id, job_id, request_id, checkpoint_sha256)
            )
            db.execute(
                "DELETE FROM worker_update_pause_requests WHERE worker_id=? "
                "AND job_id=? AND request_id=? AND phase='acknowledged'",
                (worker_id, job_id, request_id)
            )
        return {"worker_id": worker_id, "job_id": job_id, "state": "running"}

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
            if db.execute(
                "SELECT 1 FROM worker_update_resume WHERE worker_id=?", (worker_id,)
            ).fetchone():
                raise ValueError("Resume checkpoint pending; cleanup blocked")
            db.execute(
                "DELETE FROM worker_update_pause_requests WHERE worker_id=?", (worker_id,)
            )
