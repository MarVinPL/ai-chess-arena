"""Coordinator-owned assignment delivery for an Android chess inference Worker.

Arena retains sole ownership of chess positions, legal-move validation and
checkpoints. This module gives an authenticated worker a read-only copy of a
specifically assigned inference request; it never runs an LLM or moves pieces.
"""
import json
import sqlite3
import time

import chess

from remote_worker_jobs import DurableWorkerJobs
from remote_worker_protocol import validate_job, validate_result

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
            db.execute("""CREATE TABLE IF NOT EXISTS worker_inference_results (
                worker_id TEXT NOT NULL,
                job_id TEXT PRIMARY KEY,
                result_json TEXT NOT NULL,
                received_at REAL NOT NULL
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS worker_inference_capabilities (
                worker_id TEXT PRIMARY KEY,
                model_sha256 TEXT NOT NULL,
                checked_at REAL NOT NULL
            )""")

    def register_local_ready(self, worker_id, model_sha256):
        """Machine-token authenticated, fail-closed readiness from a phone.

        Only an idle client without existing assignments, holds or checkpoints
        may announce a model. The alias is a routing hint, not cryptographic
        verification of the GGUF bytes.
        """
        self.jobs._id(worker_id)
        if not isinstance(model_sha256, str) or len(model_sha256) != 64 or not all(
            c in "0123456789abcdef" for c in model_sha256
        ):
            raise ValueError("Invalid model digest")
        now = self.clock()
        with self.jobs._db() as db:
            db.execute("BEGIN IMMEDIATE")
            self.jobs._reject_update_hold(db, worker_id)
            self.jobs._reject_pending_pause(db, worker_id)
            self.jobs._reject_pending_resume(db, worker_id)
            if db.execute(
                "SELECT 1 FROM worker_inference_assignments WHERE worker_id=?",
                (worker_id,),
            ).fetchone():
                return "blocked"
            row = db.execute(
                "SELECT state,job_id,deadline FROM worker_job_safety WHERE worker_id=?",
                (worker_id,),
            ).fetchone()
            if row == ("unknown", None, None):
                recovery = db.execute(
                    "SELECT released_at FROM worker_idle_update_recovery WHERE worker_id=?",
                    (worker_id,),
                ).fetchone()
                if not recovery or not (0 <= now - recovery[0] <= 86400):
                    # Unknown crash state or expired proof: never take over
                    # another job merely because the phone claims readiness.
                    return "blocked"
                restored = db.execute(
                    """UPDATE worker_job_safety SET state='idle_confirmed',
                    updated_at=? WHERE worker_id=? AND state='unknown'
                    AND job_id IS NULL AND deadline IS NULL""",
                    (now, worker_id),
                )
                if restored.rowcount != 1:
                    raise ValueError("Worker state changed during idle-update recovery")
                db.execute(
                    "DELETE FROM worker_idle_update_recovery WHERE worker_id=?",
                    (worker_id,),
                )
            elif row is not None and row != ("idle_confirmed", None, None):
                # A running job, UNKNOWN with another job, or crash without
                # the verified update receipt must NOT become idle.
                return "blocked"
            elif row is None:
                # First authenticated registration with no previous lease.
                db.execute(
                    """INSERT INTO worker_job_safety
                    (worker_id,job_id,state,deadline,updated_at)
                    VALUES (?,NULL,'idle_confirmed',NULL,?)""",
                    (worker_id, now),
                )
            else:
                db.execute(
                    """UPDATE worker_job_safety SET updated_at=?
                    WHERE worker_id=? AND state='idle_confirmed' AND job_id IS NULL
                    AND deadline IS NULL""",
                    (now, worker_id),
                )
            db.execute(
                """INSERT INTO worker_inference_capabilities VALUES (?,?,?)
                ON CONFLICT(worker_id) DO UPDATE SET
                model_sha256=excluded.model_sha256, checked_at=excluded.checked_at""",
                (worker_id, model_sha256, now),
            )
        return "ready"

    def find_ready_worker(self, model_sha256):
        """Select an idle phone for an exact, recent model alias.

        Read-only hint; assign_from_arena must recheck the same evidence
        under BEGIN IMMEDIATE, so a race cannot claim an already busy phone.
        """
        if not isinstance(model_sha256, str) or len(model_sha256) != 64 or not all(
            c in "0123456789abcdef" for c in model_sha256
        ):
            raise ValueError("Invalid requested model")
        now = self.clock()
        with self.jobs._db() as db:
            row = db.execute(
                """SELECT c.worker_id FROM worker_inference_capabilities c
                JOIN worker_job_safety s ON s.worker_id=c.worker_id
                WHERE c.model_sha256=? AND c.checked_at BETWEEN ? AND ?
                AND s.state='idle_confirmed' AND s.job_id IS NULL
                AND NOT EXISTS (SELECT 1 FROM worker_inference_assignments a
                                WHERE a.worker_id=c.worker_id)
                AND NOT EXISTS (SELECT 1 FROM worker_update_holds h
                                WHERE h.worker_id=c.worker_id)
                AND NOT EXISTS (SELECT 1 FROM worker_update_pause_requests p
                                WHERE p.worker_id=c.worker_id)
                AND NOT EXISTS (SELECT 1 FROM worker_update_resume r
                                WHERE r.worker_id=c.worker_id)
                ORDER BY c.checked_at DESC, c.worker_id ASC LIMIT 1""",
                (model_sha256, now - 30, now),
            ).fetchone()
        return row[0] if row else None

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
            capability = db.execute(
                "SELECT model_sha256,checked_at FROM worker_inference_capabilities WHERE worker_id=?",
                (worker_id,),
            ).fetchone()
            if not existing or existing != ("idle_confirmed", None):
                raise ValueError("Worker must first be confirmed idle")
            if not capability or capability[0] != validated["model_sha256"] or not (
                0 <= now - capability[1] <= 30
            ):
                raise ValueError("Worker did not recently confirm the requested local model")
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
            completed = db.execute(
                "SELECT 1 FROM worker_inference_results WHERE worker_id=? AND job_id=?",
                (worker_id, row[0] if row else ""),
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
        if completed:
            # Model already sent its legal UCI move. Only the Arena runner
            # may apply it and release the lease. Do not infer again.
            return {"state": "awaiting_arena"}
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

    def read_arena_result(self, worker_id, job_id):
        """Trusted coordinator consumer. Read result without applying chess."""
        self.jobs._id(worker_id)
        self.jobs._id(job_id)
        with self.jobs._db() as db:
            row = db.execute(
                "SELECT job_json FROM worker_inference_assignments WHERE worker_id=? AND job_id=?",
                (worker_id, job_id),
            ).fetchone()
            result = db.execute(
                "SELECT result_json FROM worker_inference_results WHERE worker_id=? AND job_id=?",
                (worker_id, job_id),
            ).fetchone()
            lease = db.execute(
                "SELECT state,job_id,deadline FROM worker_job_safety WHERE worker_id=?",
                (worker_id,),
            ).fetchone()
        if row is None or not lease or lease[0] != "running" or lease[1] != job_id:
            raise ValueError("Worker job lease is not active")
        if lease[2] is None or lease[2] <= self.clock():
            raise ValueError("Worker result deadline expired")
        if result is None:
            return None
        job = validate_job(json.loads(row[0]))
        move = validate_result(json.loads(result[0]), job, chess.Board(job["fen"]))
        return move.uci()

    def finalize_arena_result(self, worker_id, job_id, uci, *,
                              tournament_id, checkpoint):
        """Atomically persist Arena's move checkpoint AND release phone lease.

        The phone never writes a board/checkpoint. A crash before this
        transaction commits leaves the original checkpoint, receipt and lease
        intact. A crash after it commits leaves the new checkpoint and an idle
        worker. Repeated finalizations are rejected instead of replayed.
        """
        import hashlib
        import tournament_checkpoint

        self.jobs._id(worker_id)
        self.jobs._id(job_id)
        if not isinstance(uci, str) or len(uci) not in (4, 5):
            raise ValueError("Invalid UCI finalization")
        if type(tournament_id) is not int or tournament_id <= 0:
            raise ValueError("Invalid Arena tournament ID")
        if not isinstance(checkpoint, dict) or (
            checkpoint.get("schema_version") != tournament_checkpoint.SCHEMA_VERSION
            or checkpoint.get("tournament_id") != tournament_id
        ):
            raise ValueError("Invalid next Arena checkpoint")
        now = self.clock()
        with self.jobs._db() as db:
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            self.jobs._reject_update_hold(db, worker_id)
            self.jobs._reject_pending_pause(db, worker_id)
            self.jobs._reject_pending_resume(db, worker_id)
            row = db.execute(
                "SELECT job_json FROM worker_inference_assignments WHERE worker_id=? AND job_id=?",
                (worker_id, job_id),
            ).fetchone()
            received = db.execute(
                "SELECT result_json FROM worker_inference_results WHERE worker_id=? AND job_id=?",
                (worker_id, job_id),
            ).fetchone()
            lease = db.execute(
                "SELECT state,job_id,deadline FROM worker_job_safety WHERE worker_id=?",
                (worker_id,),
            ).fetchone()
            if not row or not received or not lease or lease[0] != "running" or (
                lease[1] != job_id or lease[2] is None or lease[2] <= now
            ):
                raise ValueError("No live remote result to finalize")
            job = validate_job(json.loads(row[0]))
            before_board = chess.Board(job["fen"])
            move = validate_result(json.loads(received[0]), job, before_board)
            if move.uci() != uci:
                raise ValueError("Arena attempted to finalize a different UCI")
            before_board.push(move)
            expected_fen = before_board.fen()

            # Validate continuation of the *persisted* Arena checkpoint,
            # not merely a fresh board proposed by any caller.
            old = db.execute(
                "SELECT payload_json,sha256 FROM tournament_checkpoints WHERE tournament_id=?",
                (tournament_id,),
            ).fetchone()
            if old is None or hashlib.sha256(old[0].encode("utf-8")).hexdigest() != old[1]:
                raise ValueError("Missing or corrupted predecessor checkpoint")
            predecessor = json.loads(old[0])
            # The new checkpoint is allowed to change ONLY per-move fields.
            # Tournament metadata, pairing, player definitions and phase must
            # be identical to the persisted predecessor. Otherwise a remote
            # result could accidentally bless a stale/rewritten tournament
            # snapshot while still carrying the same FEN and legal UCI.
            mutable_fields = frozenset({
                "moves", "move_times", "fen", "side_to_move", "updated_at",
            })
            unchanged_before = {
                k: v for k, v in predecessor.items() if k not in mutable_fields
            }
            unchanged_after = {
                k: v for k, v in checkpoint.items() if k not in mutable_fields
            }
            if unchanged_before != unchanged_after:
                raise ValueError("Arena checkpoint metadata changed during inference")
            previous_moves = predecessor.get("moves")
            next_moves = checkpoint.get("moves")
            previous_times = predecessor.get("move_times")
            next_times = checkpoint.get("move_times")
            if (predecessor.get("fen") != job["fen"]
                or predecessor.get("pairing") != checkpoint.get("pairing")
                or predecessor.get("white") != checkpoint.get("white")
                or predecessor.get("black") != checkpoint.get("black")
                or not isinstance(previous_moves, list)
                or not isinstance(next_moves, list)
                or len(next_moves) != len(previous_moves) + 1
                or next_moves[:-1] != previous_moves
                or not isinstance(previous_times, list)
                or not isinstance(next_times, list)
                or len(next_times) != len(previous_times) + 1
                or next_times[:-1] != previous_times
                or not isinstance(next_moves[-1], dict)
                or next_moves[-1].get("uci") != uci
                or next_moves[-1].get("fen") != expected_fen
                or next_moves[-1].get("ply") != len(next_moves)
                or checkpoint.get("fen") != expected_fen
                or checkpoint.get("side_to_move") != (
                    "white" if before_board.turn else "black"
                )):
                raise ValueError("Arena checkpoint does not extend the assigned chess move")

            tournament_checkpoint.save_in_transaction(
                db, tournament_id, checkpoint, expected_sha256=old[1],
            )
            db.execute(
                "DELETE FROM worker_inference_results WHERE worker_id=? AND job_id=?",
                (worker_id, job_id),
            )
            db.execute(
                "DELETE FROM worker_inference_assignments WHERE worker_id=? AND job_id=?",
                (worker_id, job_id),
            )
            status = db.execute(
                """UPDATE worker_job_safety SET state='idle_confirmed',
                job_id=NULL,deadline=NULL,updated_at=?
                WHERE worker_id=? AND job_id=? AND state='running' AND deadline>?""",
                (now, worker_id, job_id, now),
            )
            if status.rowcount != 1:
                raise ValueError("Concurrent Worker lease change")
        return move

    def submit_inference_result(self, worker_id, result):
        """Validate a proposed UCI using Arena's authoritative FEN.

        This stores only the result in Arena's database. Applying a move and
        committing a chess checkpoint belong to the trusted tournament runner,
        NOT this Worker HTTP endpoint. Duplicate acknowledgments are safe.
        """
        self.jobs._id(worker_id)
        if not isinstance(result, dict):
            raise ValueError("Invalid inference result")
        now = self.clock()
        with self.jobs._db() as db:
            db.execute("BEGIN IMMEDIATE")
            self.jobs._reject_update_hold(db, worker_id)
            self.jobs._reject_pending_pause(db, worker_id)
            self.jobs._reject_pending_resume(db, worker_id)
            row = db.execute(
                "SELECT job_id,job_json FROM worker_inference_assignments WHERE worker_id=?",
                (worker_id,),
            ).fetchone()
            lease = db.execute(
                "SELECT job_id,state,deadline FROM worker_job_safety WHERE worker_id=?",
                (worker_id,),
            ).fetchone()
            if not row or not lease or lease[0] != row[0] or (
                lease[1] != "running" or lease[2] is None or lease[2] <= now
            ):
                raise ValueError("Inference result has no valid execution lease")
            job = validate_job(json.loads(row[1]))
            if job["job_id"] != row[0]:
                raise ValueError("Inference job was changed")
            move = validate_result(result, job, chess.Board(job["fen"]))
            canonical = json.dumps(
                {"version": 1, "job_id": job["job_id"], "uci": move.uci()},
                sort_keys=True, separators=(",", ":"), allow_nan=False,
            )
            existing = db.execute(
                "SELECT worker_id,result_json FROM worker_inference_results WHERE job_id=?",
                (job["job_id"],),
            ).fetchone()
            if existing:
                if existing != (worker_id, canonical):
                    raise ValueError("Conflicting inference result for same job")
                return {"state": "already_received", "job_id": job["job_id"]}
            db.execute(
                "INSERT INTO worker_inference_results VALUES (?,?,?,?)",
                (worker_id, job["job_id"], canonical, now),
            )
        return {"state": "received_not_applied", "job_id": job["job_id"]}
