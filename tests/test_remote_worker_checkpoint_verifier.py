"""Real chess-specific checkpoint validation before accepting remote resume."""
import chess
import pytest

from remote_worker_checkpoint_verifier import verify_chess_checkpoint
from remote_worker_jobs import DurableWorkerJobs
from remote_worker_update_pause import WorkerUpdatePause


WORKER = "worker_12345678"
JOB = "job_12345678"


def checkpoint(moves=("e2e4", "e7e5", "g1f3")):
    board = chess.Board()
    for move in moves:
        board.push_uci(move)
    return {
        "schema_version": 1, "job_id": JOB, "resume_mode": "resume",
        "payload": {
            "initial_fen": chess.STARTING_FEN,
            "moves_uci": list(moves),
            "current_fen": board.fen(),
        },
    }


def test_replay_accepts_only_exact_legal_game_position():
    assert verify_chess_checkpoint(checkpoint())
    assert verify_chess_checkpoint(checkpoint(()))
    assert verify_chess_checkpoint({
        "schema_version": 1, "job_id": JOB, "resume_mode": "restart", "payload": {}
    })
    damaged = checkpoint()
    damaged["payload"]["moves_uci"][1] = "e7e4"
    assert not verify_chess_checkpoint(damaged)
    damaged = checkpoint()
    damaged["payload"]["current_fen"] = chess.STARTING_FEN
    assert not verify_chess_checkpoint(damaged)
    damaged = checkpoint()
    damaged["payload"]["moves_uci"] = ["e2e4"] * 3000
    assert not verify_chess_checkpoint(damaged)
    damaged = checkpoint()
    damaged["payload"]["initial_fen"] = "invalid"
    assert not verify_chess_checkpoint(damaged)
    damaged = checkpoint()
    damaged["payload"]["moves_uci"] = ["e2e4", "e7e5", "g1f9"]
    assert not verify_chess_checkpoint(damaged)
    damaged = checkpoint()
    damaged["payload"]["unexpected"] = True
    assert not verify_chess_checkpoint(damaged)
    damaged = checkpoint()
    damaged["job_id"] = "bad"
    assert not verify_chess_checkpoint(damaged)
    damaged = checkpoint()
    damaged["schema_version"] = True
    assert not verify_chess_checkpoint(damaged)
    damaged = checkpoint()
    damaged["resume_mode"] = "restart"
    assert not verify_chess_checkpoint(damaged)


def test_chess_verifier_is_usable_as_coordinator_pause_gate(tmp_path):
    db = tmp_path / "chess.db"
    now = [100.]
    jobs = DurableWorkerJobs(db, clock=lambda: now[0])
    with jobs._db() as conn:
        conn.execute(
            "INSERT INTO worker_job_safety VALUES (?,?,'running',?,?)",
            (WORKER, JOB, now[0] + 120, now[0]),
        )
    pause = WorkerUpdatePause(db, clock=lambda: now[0])
    request_id = pause.request(WORKER, JOB)
    pause.acknowledge(WORKER, JOB, request_id,
                      worker_stopped=True, checkpoint_verified=True)
    illegal = checkpoint()
    illegal["payload"]["moves_uci"] = ["e2e5"]
    with pytest.raises(ValueError, match="verified"):
        pause.finalize_verified_pause(
            WORKER, JOB, request_id, hold_token="a" * 64,
            checkpoint=illegal, worker_stopped_verified=True,
            checkpoint_verifier=verify_chess_checkpoint,
        )
    assert pause.read_resume_checkpoint(WORKER) is None
    valid = checkpoint()
    pause.finalize_verified_pause(
        WORKER, JOB, request_id, hold_token="a" * 64,
        checkpoint=valid, worker_stopped_verified=True,
        checkpoint_verifier=verify_chess_checkpoint,
    )
    saved = pause.read_resume_checkpoint(WORKER)
    assert saved["checkpoint"] == valid
    jobs.release_update_hold(WORKER, "a" * 64)
    assert pause.reconcile_resume(
        WORKER, JOB, request_id,
        checkpoint_sha256=saved["sha256"], timeout_seconds=120,
        worker_ready_verified=True, resume_accepted_verified=True,
        checkpoint_verifier=verify_chess_checkpoint,
    )["state"] == "running"
