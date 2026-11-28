"""Read-only, fail-closed production quiescence check for phone staging."""
import hashlib
import json
import math
import chess
import sqlite3
import subprocess
import sys
from pathlib import Path


def validate_history(payload):
    # The current staging experiment uses protocol v2/classic chess.
    # Unknown or elimination checkpoints require a separately reviewed guard.
    if payload.get("variant", "standard") != "standard":
        raise ValueError("Unsupported stopped checkpoint variant")
    board = chess.Board()
    times = payload.get("move_times")
    moves = payload["moves"]
    if not isinstance(times, list) or len(times) != len(moves):
        raise ValueError("Checkpoint move times mismatch")
    for ply, (item, seconds) in enumerate(zip(moves, times), 1):
        if (not isinstance(item, dict) or item.get("ply") != ply
                or item.get("color") != ("white" if board.turn else "black")
                or type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds < 0):
            raise ValueError("Invalid checkpoint history")
        move = chess.Move.from_uci(item["uci"])
        if move not in board.legal_moves:
            raise ValueError("Illegal checkpoint move")
        board.push(move)
        if item.get("fen") != board.fen():
            raise ValueError("Checkpoint intermediate FEN mismatch")
    if payload["fen"] != board.fen() or payload.get("side_to_move") != ("white" if board.turn else "black"):
        raise ValueError("Checkpoint final FEN mismatch")


def check_database(db_path):
    with sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        for tid, state, blocked in db.execute("SELECT id,status,resume_blocked FROM tournaments"):
            if state in ("planned", "completed"):
                continue
            if state != "stopped" or blocked != 0:
                raise ValueError("Unsafe tournament state")
            row = db.execute(
                "SELECT schema_version,payload_json,sha256 FROM tournament_checkpoints WHERE tournament_id=?", (tid,)
            ).fetchone()
            if not row or row[0] != 1 or hashlib.sha256(row[1].encode()).hexdigest() != row[2]:
                raise ValueError("Missing or corrupt stopped checkpoint")
            payload = json.loads(row[1])
            if (payload.get("schema_version") != 1 or payload.get("tournament_id") != tid
                    or payload.get("phase") != "game" or not isinstance(payload.get("moves"), list)
                    or not isinstance(payload.get("fen"), str)):
                raise ValueError("Invalid stopped checkpoint")
            validate_history(payload)
            pair = payload.get("pairing", {}).get("id")
            running = db.execute(
                "SELECT id,white_model_id,black_model_id,stage FROM tournament_pairings WHERE tournament_id=? AND status='running'", (tid,)
            ).fetchall()
            if type(pair) is not int or running != [(pair, payload["pairing"].get("white_model_id"), payload["pairing"].get("black_model_id"), payload["pairing"].get("stage"))]:
                raise ValueError("Stopped checkpoint pairing mismatch")
        if db.execute("""SELECT count(*) FROM tournament_pairings p JOIN tournaments t
                       ON t.id=p.tournament_id WHERE p.status='running' AND t.status!='stopped'""").fetchone()[0]:
            raise ValueError("Unexpected running pairing")
        for table in ("worker_inference_assignments", "worker_inference_results",
                      "worker_update_pause_requests", "worker_update_resume", "worker_update_holds"):
            if db.execute("SELECT count(*) FROM " + table).fetchone()[0]:
                raise ValueError("Outstanding worker operation: " + table)
        if db.execute("SELECT count(*) FROM worker_job_safety WHERE state IS NULL OR state!='idle_confirmed' OR job_id IS NOT NULL").fetchone()[0]:
            raise ValueError("Worker is not idle")
        print("PRODUCTION_DB_QUIESCENT=PASS")


def check_processes(cgroup_root=Path("/sys/fs/cgroup"), proc_root=Path("/proc")):
    fields = subprocess.check_output(
        ["systemctl", "show", "ai-chess-arena.service", "-p", "MainPID", "-p", "ControlGroup", "-p", "ActiveState"],
        text=True,
    )
    props = dict(line.split("=", 1) for line in fields.splitlines())
    if props.get("ActiveState") != "active" or int(props["MainPID"]) <= 0:
        raise ValueError("Production service not active")
    root = cgroup_root / props["ControlGroup"].lstrip("/")
    if props["ControlGroup"] != "/system.slice/ai-chess-arena.service" or not root.is_dir():
        raise ValueError("Cannot verify production cgroup")
    pids = {int(p) for f in [root / "cgroup.procs", *root.glob("**/cgroup.procs")] for p in f.read_text().split()}
    if pids != {int(props["MainPID"])}:
        raise ValueError("Unexpected production child processes")
    for path in proc_root.glob("[0-9]*/comm"):
        try:
            name = path.read_text().strip().lower()
        except FileNotFoundError:
            continue
        if name.startswith(("llama", "llamafile", "arena-runtime")):
            raise ValueError("Inference process present")
    print("PRODUCTION_PROCESS_QUIESCENT=PASS")


if __name__ == "__main__":
    try:
        check_processes()
        check_database(sys.argv[1])
    except (OSError, ValueError, TypeError, AttributeError, KeyError, sqlite3.Error, subprocess.SubprocessError) as exc:
        sys.exit("Staging preflight refused: " + str(exc))
