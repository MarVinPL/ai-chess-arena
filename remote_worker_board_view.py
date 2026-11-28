"""Read-only, worker-bound chessboard view from the Arena's authoritative live state.

No second board, move history or checkpoint is stored here. A global /api/live
event must never be shown as belonging to a particular Android Worker unless
the coordinator's current assignment AND the live event explicitly agree.
"""
import re
import sqlite3
import time

from elimination_chess import board_for_variant

_WORKER_ID = re.compile(r"[A-Za-z0-9_-]{8,96}\Z")
_ACTIVE_STAGES = frozenset({
    "game_prepare", "game_ready", "game_running", "generating",
    "move_played", "model_response", "model_retry",
})


def worker_board_snapshot(db_path, worker_id, live, *, now=None):
    """Return a minimal bounded snapshot, or an inactive status with no FEN."""
    if not isinstance(worker_id, str) or not _WORKER_ID.fullmatch(worker_id):
        raise ValueError("Invalid worker identity")
    now = time.time() if now is None else now
    with sqlite3.connect(str(db_path), timeout=10) as db:
        assignment = db.execute(
            "SELECT job_id,state,deadline FROM worker_job_safety WHERE worker_id=?",
            (worker_id,),
        ).fetchone()
        hold = db.execute(
            "SELECT 1 FROM worker_update_holds WHERE worker_id=?", (worker_id,)
        ).fetchone()
        pause = db.execute(
            "SELECT 1 FROM worker_update_pause_requests WHERE worker_id=?", (worker_id,)
        ).fetchone()
        resume = db.execute(
            "SELECT 1 FROM worker_update_resume WHERE worker_id=?", (worker_id,)
        ).fetchone()
    if hold or pause or resume:
        return {"active": False, "state": "paused"}
    if not assignment or assignment[1] != "running" or not (
        isinstance(assignment[2], (float, int)) and assignment[2] > now
    ):
        return {"active": False, "state": "no_active_job"}
    job_id = assignment[0]
    if (
        not isinstance(live, dict)
        or live.get("worker_id") != worker_id
        or live.get("job_id") != job_id
        or live.get("stage") not in _ACTIVE_STAGES
    ):
        return {"active": False, "state": "not_bound_to_worker"}
    updated = live.get("updated_at")
    if isinstance(updated, bool) or not isinstance(updated, (float, int)) or not 0 <= now - updated <= 30:
        return {"active": False, "state": "stale"}
    fen = live.get("fen")
    if not isinstance(fen, str) or len(fen) > 120:
        return {"active": False, "state": "invalid_board"}
    try:
        board = board_for_variant(fen, live.get("variant", "standard"))
    except ValueError:
        return {"active": False, "state": "invalid_board"}
    last = live.get("last_move")
    uci = last.get("uci") if isinstance(last, dict) else None
    if not isinstance(uci, str) or not re.fullmatch(r"[a-h][1-8][a-h][1-8][qrbn]?", uci):
        uci = None

    def label(key):
        value = live.get(key)
        return value[:96] if isinstance(value, str) else ""

    return {
        "active": True, "state": "running", "fen": board.fen(),
        "job_id": job_id, "white": label("white"), "black": label("black"),
        "model": label("model"), "stage": live["stage"], "last_move_uci": uci,
        "updated_at_unix_ms": int(updated * 1000),
    }
