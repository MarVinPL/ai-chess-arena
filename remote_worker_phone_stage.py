"""One-shot, isolated Android Worker HTTPS E2E coordinator (Issue #276).

The network listener is *only* 127.0.0.1. TLS must be terminated by an
explicitly configured Caddy site. No production database paths or admin
capabilities are accepted by the HTTP server. Stage storage must be private.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import chess

import tournament_checkpoint
from remote_worker_inference_delivery import WorkerInferenceDelivery
from remote_worker_phone_api import ROUTES, handle_phone_request
from remote_worker_tokens import WorkerTokenStore

TEST_TOURNAMENT_ID = 1
WORKER_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{8,96}\Z")
SHA_PATTERN = re.compile(r"[0-9a-f]{64}\Z")


def _open_db(db_path):
    return sqlite3.connect(str(db_path), timeout=10)


def prepare_stage(test_db: Path, source_db: Path, worker_id: str,
                  expected_sha: str, *, lifetime_seconds: int = 3600):
    """Copy ONLY the SHA-256 token verifier (not the secret) from read-only prod.

    The temporary test database expires sooner than the production credential.
    The source DB is opened with SQLite mode=ro and is never written.
    """
    if not isinstance(worker_id, str) or not WORKER_ID_PATTERN.fullmatch(worker_id):
        raise ValueError("Invalid worker ID")
    if not isinstance(expected_sha, str) or not SHA_PATTERN.fullmatch(expected_sha):
        raise ValueError("Invalid expected model digest")
    if not 60 <= lifetime_seconds <= 3600:
        raise ValueError("Invalid test lifetime")
    if test_db.exists():
        raise ValueError("The test DB already exists; refuse to overwrite")
    if source_db.resolve() == test_db.resolve():
        raise ValueError("Production and test DB must differ")
    test_db.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    if stat.S_IMODE(test_db.parent.stat().st_mode) & 0o077:
        raise ValueError("Staging DB directory must not be accessible to others")
    with sqlite3.connect(f"file:{source_db}?mode=ro", uri=True) as source:
        row = source.execute(
            "SELECT token_digest,expires_at,revoked_at FROM worker_tokens WHERE worker_id=?",
            (worker_id,),
        ).fetchone()
    now = int(time.time())
    if not row or row[2] is not None or row[1] <= now:
        raise ValueError("Worker token missing, expired or revoked")
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    descriptor = os.open(test_db, flags, 0o600)
    os.close(descriptor)
    try:
        WorkerTokenStore(test_db)
        with _open_db(test_db) as db:
            db.execute(
                "INSERT INTO worker_tokens VALUES(?,?,?,NULL)",
                (worker_id, row[0], min(int(row[1]), now + lifetime_seconds)),
            )
            db.execute("CREATE TABLE tournaments (id INTEGER PRIMARY KEY)")
            db.execute("INSERT INTO tournaments VALUES (?)", (TEST_TOURNAMENT_ID,))
            db.execute(
                """CREATE TABLE stage_control (
                    id INTEGER PRIMARY KEY CHECK(id=1),
                    phase TEXT NOT NULL,
                    worker_id TEXT NOT NULL,
                    expected_sha TEXT NOT NULL,
                    job_id TEXT,
                    changed_at REAL NOT NULL
                )"""
            )
            db.execute(
                "INSERT INTO stage_control VALUES (1,'unarmed',?,?,NULL,?)",
                (worker_id, expected_sha, time.time()),
            )
        tournament_checkpoint.ensure_schema(test_db)
        checkpoint = {
            "schema_version": tournament_checkpoint.SCHEMA_VERSION,
            "tournament_id": TEST_TOURNAMENT_ID,
            "pairing": {"id": 1, "stage": "isolated"},
            "white": {"name": "Android Worker test"},
            "black": {"name": "Arena test"},
            "game_started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "moves": [],
            "move_times": [],
            "fen": chess.STARTING_FEN,
            "side_to_move": "white",
        }
        tournament_checkpoint.save(test_db, TEST_TOURNAMENT_ID, checkpoint)
        WorkerInferenceDelivery(test_db)
    except BaseException:
        test_db.unlink(missing_ok=True)
        raise


def arm_stage(test_db: Path):
    with _open_db(test_db) as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT phase FROM stage_control WHERE id=1").fetchone()
        if not row or row[0] != "unarmed":
            raise ValueError("Test is not unarmed; cannot run another move")
        db.execute(
            "UPDATE stage_control SET phase='armed',changed_at=? WHERE id=1",
            (time.time(),),
        )


def status_stage(test_db: Path):
    with _open_db(test_db) as db:
        state = db.execute(
            "SELECT phase,worker_id,expected_sha,job_id FROM stage_control WHERE id=1"
        ).fetchone()
        if state is None:
            raise ValueError("Missing test control")
        capability = db.execute(
            "SELECT checked_at FROM worker_inference_capabilities WHERE worker_id=?",
            (state[1],),
        ).fetchone() if db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='worker_inference_capabilities'"
        ).fetchone() else None
        result = {
            "phase": state[0],
            "worker_id": state[1],
            "model_sha256": state[2],
            "job_id": state[3],
            "ready_seconds_ago": round(time.time() - capability[0], 1) if capability else None,
        }
    cp = tournament_checkpoint.load(test_db, TEST_TOURNAMENT_ID)
    result["moves"] = [
        {"ply": m["ply"], "uci": m["uci"], "fen": m["fen"]}
        for m in cp["moves"]
    ]
    return result


class PhoneStage:
    def __init__(self, db_path: Path):
        self.db = db_path
        self.lock = threading.RLock()
        self.live: dict = {}

    def on_ready(self, response):
        if response.get("state") != "ready" or response.get("authenticated") is not True:
            return
        with self.lock:
            with _open_db(self.db) as db:
                state = db.execute(
                    "SELECT phase,worker_id,expected_sha FROM stage_control WHERE id=1"
                ).fetchone()
            if not state or state[0] != "armed" or state[1] != response.get("worker_id"):
                return
            worker_id, expected_sha = state[1], state[2]
            delivery = WorkerInferenceDelivery(self.db)
            if delivery.find_ready_worker(expected_sha) != worker_id:
                return
            job_id = "stage_" + os.urandom(16).hex()
            job = {
                "version": 2,
                "job_id": job_id,
                "fen": chess.STARTING_FEN,
                "legal_moves": sorted(move.uci() for move in chess.Board().legal_moves),
                "model_sha256": expected_sha,
                "max_tokens": 32,
                "timeout_seconds": 300,
            }
            delivery.assign_from_arena(worker_id, job)
            with _open_db(self.db) as db:
                db.execute(
                    "UPDATE stage_control SET phase='assigned',job_id=?,changed_at=? WHERE id=1",
                    (job_id, time.time()),
                )
            self.live = {
                "worker_id": worker_id, "job_id": job_id,
                "stage": "generating", "fen": chess.STARTING_FEN,
                "white": "Phone test", "black": "Arena test",
                "model": "Llama 3.2 1B", "updated_at": time.time(),
            }

    def on_result(self, response):
        if response.get("state") not in ("received_not_applied", "already_received"):
            return
        if response.get("authenticated") is not True:
            return
        with self.lock:
            with _open_db(self.db) as db:
                state = db.execute(
                    "SELECT phase,worker_id,job_id FROM stage_control WHERE id=1"
                ).fetchone()
            if not state or state[0] != "assigned" or state[1] != response.get("worker_id"):
                return
            if response.get("job_id") != state[2]:
                return
            worker_id, job_id = state[1], state[2]
            delivery = WorkerInferenceDelivery(self.db)
            uci = delivery.read_arena_result(worker_id, job_id)
            if not uci:
                return
            previous = tournament_checkpoint.load(self.db, TEST_TOURNAMENT_ID)
            board = chess.Board(previous["fen"])
            move = chess.Move.from_uci(uci)
            if move not in board.legal_moves:
                raise ValueError("Untrusted remote move is not legal")
            board.push(move)
            next_checkpoint = {
                **previous,
                "moves": [*previous["moves"], {
                    "uci": uci,
                    "ply": 1,
                    "color": "white",
                    "fen": board.fen(),
                    "seconds": 0.0,
                }],
                "move_times": [*previous["move_times"], 0.0],
                "fen": board.fen(),
                "side_to_move": "black",
            }
            delivery.finalize_arena_result(
                worker_id, job_id, uci,
                tournament_id=TEST_TOURNAMENT_ID,
                checkpoint=next_checkpoint,
            )
            with _open_db(self.db) as db:
                db.execute(
                    "UPDATE stage_control SET phase='completed',changed_at=? WHERE id=1",
                    (time.time(),),
                )
            self.live = {}


class StageHandler(BaseHTTPRequestHandler):
    server_version = "ArenaAndroidStage/1"
    def send_json(self, message, code=200):
        self.last_response_code = code
        self.last_response = message
        encoded = json.dumps(message, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format, *args):
        # Never log request targets, secret headers or token-bearing payloads.
        return

    def do_GET(self):
        if urlsplit(self.path).path != "/health":
            self.send_json({"error": "Not found"}, 404)
        else:
            self.send_json({"ok": True, "service": "isolated-phone-e2e"})

    def do_POST(self):
        path = urlsplit(self.path).path
        stage: PhoneStage = self.server.stage
        if path == "/api/remote-workers/authenticate":
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 2048:
                    raise ValueError("Invalid length")
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict) or set(body) != {"worker_id", "token"}:
                    raise ValueError("Invalid auth payload")
            except (ValueError, TypeError):
                self.send_json({"error": "Invalid authentication request"}, 400)
                return
            valid = WorkerTokenStore(stage.db).authenticate(
                body.get("worker_id"), body.get("token"),
            )
            if valid:
                self.send_json({"authenticated": True, "worker_id": body["worker_id"]})
            else:
                self.send_json({"authenticated": False}, 401)
            return
        if path not in ROUTES:
            self.send_json({"error": "Not found"}, 404)
            return
        handle_phone_request(self, path, stage.db, dict(stage.live))
        if self.last_response_code == 200:
            if path.endswith("/ready"):
                stage.on_ready(self.last_response)
            if path.endswith("/job-result"):
                stage.on_result(self.last_response)


def serve(db: Path, port: int):
    if not 1024 <= port <= 65535:
        raise ValueError("Unsafe port")
    if db.resolve() == Path("/var/lib/ai-chess-arena/arena.sqlite3").resolve():
        raise ValueError("Serving production database is forbidden")
    if stat.S_IMODE(db.stat().st_mode) & 0o077:
        raise ValueError("Staging database is readable outside owner")
    if stat.S_IMODE(db.parent.stat().st_mode) & 0o077:
        raise ValueError("Staging directory is not private")
    if status_stage(db)["phase"] not in ("unarmed", "armed", "assigned", "completed"):
        raise ValueError("Staging control is invalid")
    ThreadingHTTPServer.allow_reuse_address = True
    server = ThreadingHTTPServer(("127.0.0.1", port), StageHandler)
    server.stage = PhoneStage(db)
    server.serve_forever(poll_interval=0.2)


def main():
    parser = argparse.ArgumentParser()
    modes = parser.add_subparsers(dest="command", required=True)
    prepare = modes.add_parser("prepare")
    prepare.add_argument("--db", type=Path, required=True)
    prepare.add_argument("--source-db", type=Path, required=True)
    prepare.add_argument("--worker-id", required=True)
    prepare.add_argument("--expected-sha", required=True)
    arm = modes.add_parser("arm")
    arm.add_argument("--db", type=Path, required=True)
    status = modes.add_parser("status")
    status.add_argument("--db", type=Path, required=True)
    server = modes.add_parser("serve")
    server.add_argument("--db", type=Path, required=True)
    server.add_argument("--port", type=int, default=8789)
    args = parser.parse_args()
    if args.command == "prepare":
        prepare_stage(args.db, args.source_db, args.worker_id, args.expected_sha)
        print("PREPARED: isolated temp DB; token verifier only; no real secret")
    elif args.command == "arm":
        arm_stage(args.db)
        print("ARMED: one test move only; requires phone /ready")
    elif args.command == "status":
        print(json.dumps(status_stage(args.db), indent=2))
    else:
        serve(args.db, args.port)


if __name__ == "__main__":
    main()
