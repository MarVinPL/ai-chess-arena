"""Isolated one-move real Android Worker experiment (Issue #275).

- Sandbox owns a separate SQLite database and tournament checkpoint.
- Only the SHA-256 token digest of one selected active Worker is imported,
  never the plaintext credential. Import is an explicit offline admin command.
- The HTTPS proxy forwards to an independently bound 127.0.0.1 listener.
- No job is created by any Worker HTTP route. 'arm' is admin-only local CLI.
- 'finalize' applies the actual submitted UCI and atomically persists the
  Arena checkpoint, then releases the same sandbox Worker's lease.
- No planner, GGUF downloader, or production tournament engine is started.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sqlite3
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import chess
import server
import tournament_checkpoint
from remote_worker_inference_delivery import WorkerInferenceDelivery
from remote_worker_phone_api import ROUTES

SANDBOX_TOURNAMENT_ID = 1
SANDBOX_PAIRING_ID = 1
DEFAULT_WORKER_ID = "android_marvin_01"
PRODUCTION_DB = Path("/var/lib/ai-chess-arena/arena.sqlite3")
MODEL_SHA = "6f85a640a97cf2bf5b8e764087b1e83da0fdb51d7c9fab7d0fece9385611df83"
DEFAULT_DB = Path("/var/lib/ai-chess-arena-phone-e2e/arena.sqlite3")
DEFAULT_PORT = 8191


def _require_separate_db(destination: Path, source: Path | None = None) -> None:
    destination = destination.resolve()
    if destination == PRODUCTION_DB.resolve():
        raise ValueError("Sandbox cannot use the production database")
    if source is not None and destination == source.resolve():
        raise ValueError("Source and sandbox databases must differ")
    if destination.exists():
        # A dedicated stage UID cannot traverse the production DB directory
        # (0700 + systemd InaccessiblePaths). That is a security property,
        # not an error. The stage file must be owned by this process, so
        # it cannot be a hardlink to the production file owned by debian.
        staged = destination.stat()
        if staged.st_uid != os.geteuid():
            raise ValueError("Sandbox database is not owned by this process")
        try:
            production = PRODUCTION_DB.stat()
        except (PermissionError, FileNotFoundError):
            production = None
        if production is not None and (
            staged.st_dev, staged.st_ino
        ) == (production.st_dev, production.st_ino):
            raise ValueError("Sandbox database is a hardlink to production")


def prepare_sandbox(destination: Path, source: Path, worker_id: str) -> None:
    """Explicit one-time token-hash copy: source opened read-only, stage writable."""
    destination, source = Path(destination), Path(source)
    _require_separate_db(destination, source)
    if destination.exists():
        raise ValueError("Sandbox already exists, refusing to replace its records")
    # Only the one selected worker may authenticate with a copied token digest.
    from remote_worker_tokens import WorkerTokenStore
    WorkerTokenStore._worker(worker_id)
    with sqlite3.connect(f"file:{source.resolve()}?mode=ro", uri=True) as live:
        live.row_factory = sqlite3.Row
        record = live.execute(
            "SELECT worker_id,token_digest,expires_at,revoked_at "
            "FROM worker_tokens WHERE worker_id=?", (worker_id,)
        ).fetchone()
        if not record or record["revoked_at"] is not None or record["expires_at"] <= int(time.time()):
            raise ValueError("Source worker token missing, expired or revoked")
        digest = bytes(record["token_digest"])
        # The copied verifier must be short-lived even if the real Worker
        # token is valid for weeks. It never rotates the original token.
        expiry = min(record["expires_at"], int(time.time()) + 3600)
        if len(digest) != 32:
            raise ValueError("Invalid source digest length")
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    old_umask = os.umask(0o077)
    try:
        with sqlite3.connect(destination) as db:
            db.execute(
                "CREATE TABLE worker_tokens (worker_id TEXT PRIMARY KEY,"
                "token_digest BLOB NOT NULL,expires_at INTEGER NOT NULL,revoked_at INTEGER)"
            )
            db.execute("INSERT INTO worker_tokens VALUES (?,?,?,NULL)",
                       (worker_id, digest, expiry))
            db.execute("CREATE TABLE tournaments (id INTEGER PRIMARY KEY)")
            db.execute("INSERT INTO tournaments VALUES (?)", (SANDBOX_TOURNAMENT_ID,))
        os.chmod(destination, 0o600)
        tournament_checkpoint.ensure_schema(destination)
        predecessor = {
            "schema_version": tournament_checkpoint.SCHEMA_VERSION,
            "tournament_id": SANDBOX_TOURNAMENT_ID,
            "phase": "game",
            "pairing": {"id": SANDBOX_PAIRING_ID},
            "white": {"name": "Phone Llama-3.2-1B"},
            "black": {"name": "Isolated test"},
            "moves": [],
            "move_times": [],
            "fen": chess.STARTING_FEN,
            "side_to_move": "white",
        }
        tournament_checkpoint.save(destination, SANDBOX_TOURNAMENT_ID, predecessor)
        WorkerInferenceDelivery(destination)
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    finally:
        os.umask(old_umask)
    print("SANDBOX_BOOTSTRAP_OK: isolated database and checkpoint created; source only read")
    print("TOKEN_IN_SANDBOX=hash_only, one worker; no plaintext imported")


class SandboxHandler(server.Handler):
    """Only the protocol routes needed by the Android app are reachable."""

    def do_GET(self):
        if urlsplit(self.path).path == "/health":
            self.send_json({"sandbox": True, "status": "ok"})
            return
        self.send_error(404)

    def do_POST(self):
        if urlsplit(self.path).path not in ROUTES | {"/api/remote-workers/authenticate"}:
            self.send_error(404)
            return
        super().do_POST()


def serve(destination: Path, *, port: int = DEFAULT_PORT) -> None:
    destination = Path(destination)
    _require_separate_db(destination)
    if not destination.is_file():
        raise ValueError("Sandbox not initialized")
    if not 1025 <= port <= 65535:
        raise ValueError("Unprivileged port required")
    # This is the only SQLite path the live sandbox Handler can see.
    server.DB = destination
    os.environ["ARENA_PHONE_WORKER_API_ENABLED"] = "1"
    os.environ["ARENA_PHONE_INFERENCE_ENABLED"] = "0"
    http = ThreadingHTTPServer(("127.0.0.1", port), SandboxHandler)
    print(f"SANDBOX_LISTEN=127.0.0.1:{port}", flush=True)
    try:
        http.serve_forever()
    finally:
        http.server_close()


def arm_one_job(destination: Path, worker_id: str, model_sha256: str = MODEL_SHA) -> str:
    """Trusted, local-only CLI operation; never reachable via HTTPS."""
    _require_separate_db(destination)
    old = tournament_checkpoint.load(destination, SANDBOX_TOURNAMENT_ID)
    if old is None or old["fen"] != chess.STARTING_FEN or old["moves"]:
        raise ValueError("Sandbox test already completed or unexpected checkpoint")
    job_id = "sandbox_" + secrets.token_hex(12)
    position = chess.Board(old["fen"])
    job = {
        "version": 2,
        "job_id": job_id,
        "fen": position.fen(),
        "model_sha256": model_sha256,
        "legal_moves": sorted(move.uci() for move in position.legal_moves),
        "max_tokens": 32,
        "timeout_seconds": 600,
    }
    WorkerInferenceDelivery(destination).assign_from_arena(worker_id, job)
    print("SANDBOX_JOB_ASSIGNED", job_id, "legal_moves", len(job["legal_moves"]))
    return job_id


def finalize_one_result(destination: Path, worker_id: str) -> str:
    """Use the coordinator's real atomic checkpoint-and-lease transaction."""
    _require_separate_db(destination)
    delivery = WorkerInferenceDelivery(destination)
    with sqlite3.connect(destination) as db:
        row = db.execute(
            "SELECT job_id FROM worker_inference_assignments WHERE worker_id=?",
            (worker_id,),
        ).fetchone()
    if not row:
        raise ValueError("No pending sandbox job")
    job_id = row[0]
    proposed = delivery.read_arena_result(worker_id, job_id)
    if not proposed:
        raise ValueError("Android has not submitted an authenticated legal UCI yet")
    previous = tournament_checkpoint.load(destination, SANDBOX_TOURNAMENT_ID)
    board = chess.Board(previous["fen"])
    move = chess.Move.from_uci(proposed)
    if move not in board.legal_moves:
        raise ValueError("Illegal move")
    board.push(move)
    record = {
        "uci": proposed, "ply": len(previous["moves"]) + 1,
        "color": "white",
        "fen": board.fen(),
        "seconds": 0.1,
    }
    updated = {
        **previous,
        "moves": previous["moves"] + [record],
        "move_times": previous["move_times"] + [0.1],
        "fen": board.fen(),
        "side_to_move": "white" if board.turn else "black",
    }
    delivery.finalize_arena_result(
        worker_id, job_id, proposed, tournament_id=SANDBOX_TOURNAMENT_ID,
        checkpoint=updated,
    )
    after = tournament_checkpoint.load(destination, SANDBOX_TOURNAMENT_ID)
    if after != updated:
        raise RuntimeError("Durable checkpoint mismatch")
    with sqlite3.connect(destination) as db:
        state = db.execute(
            "SELECT state,job_id FROM worker_job_safety WHERE worker_id=?",
            (worker_id,),
        ).fetchone()
        count = db.execute(
            "SELECT COUNT(*) FROM worker_inference_results WHERE worker_id=?",
            (worker_id,),
        ).fetchone()[0]
    if state != ("idle_confirmed", None) or count != 0:
        raise RuntimeError("Lease was not atomically released")
    print("SANDBOX_CHECKPOINT_COMMITTED UCI=" + proposed + " PLY=1")
    print("NEW_FEN=" + after["fen"])
    print("SANDBOX_WORKER_IDLE=PASS")
    return proposed


def status(destination: Path, worker_id: str) -> None:
    _require_separate_db(destination)
    checkpoint = tournament_checkpoint.load(destination, SANDBOX_TOURNAMENT_ID)
    with sqlite3.connect(destination) as db:
        ready = db.execute(
            "SELECT model_sha256,checked_at FROM worker_inference_capabilities "
            "WHERE worker_id=?", (worker_id,)
        ).fetchone()
        lease = db.execute(
            "SELECT state,job_id,deadline FROM worker_job_safety WHERE worker_id=?",
            (worker_id,)
        ).fetchone()
        result = db.execute(
            "SELECT result_json FROM worker_inference_results WHERE worker_id=?",
            (worker_id,),
        ).fetchone()
    print(json.dumps({
        "sandbox": True, "ready_digest": ready[0] if ready else None,
        "ready_age_seconds": round(time.time()-ready[1], 1) if ready else None,
        "lease": {"state": lease[0], "job_id": lease[1], "deadline": lease[2]} if lease else None,
        "result_uci": json.loads(result[0])["uci"] if result else None,
        "checkpoint_fen": checkpoint["fen"], "moves": checkpoint["moves"],
    }, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("bootstrap", "serve", "arm", "status", "finalize"))
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--source-db", type=Path, default=PRODUCTION_DB)
    parser.add_argument("--worker-id", default=DEFAULT_WORKER_ID)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--sha256", default=MODEL_SHA)
    args = parser.parse_args()
    if args.action == "bootstrap":
        prepare_sandbox(args.db, args.source_db, args.worker_id)
    elif args.action == "serve":
        serve(args.db, port=args.port)
    elif args.action == "arm":
        arm_one_job(args.db, args.worker_id, args.sha256)
    elif args.action == "status":
        status(args.db, args.worker_id)
    else:
        finalize_one_result(args.db, args.worker_id)


if __name__ == "__main__":
    main()
