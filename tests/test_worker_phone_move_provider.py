"""Arena owns game state while the Android model computes a legal UCI move."""
import sqlite3
import threading

import chess
import pytest

from remote_worker_inference_delivery import WorkerInferenceDelivery
from remote_worker_phone_move_provider import ArenaPhoneMoveProvider, PhoneMoveUnavailable

WORKER = "android_1234567890abcdef"
MODEL = "a" * 64


def prepare(tmp_path, *, clock=lambda: 1000.0):
    db = tmp_path / "remote_phone.sqlite"
    delivery = WorkerInferenceDelivery(db, clock=clock)
    assert delivery.register_local_ready(WORKER, MODEL) == "ready"
    return db, delivery


def test_arena_receives_phone_result_and_retains_board_authority(tmp_path):
    db, delivery = prepare(tmp_path)
    arena_board = chess.Board()
    event_calls = []

    def on_assignment(worker_id, job_id, fen, player):
        event_calls.append((worker_id, job_id, fen))
        assert fen == arena_board.fen()
        assert player["model_sha256"] == MODEL
        assert delivery.submit_inference_result(worker_id, {
            "version": 1, "job_id": job_id, "uci": "e2e4"
        })["state"] == "received_not_applied"

    provider = ArenaPhoneMoveProvider(
        db, WORKER, clock=lambda: 1000.0,
        on_assignment=on_assignment, sleep=lambda _: None,
    )
    move, metadata = provider(
        arena_board.copy(), {"model_sha256": MODEL, "name": "Phone"},
        threading.Event(), {"max_tokens": 32},
    )
    assert move == chess.Move.from_uci("e2e4")
    assert metadata["runtime"] == "android-local-llama-cpp"
    assert len(event_calls) == 1
    assert arena_board.fen() == chess.STARTING_FEN  # Provider never pushes
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT count(*) FROM worker_inference_results").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM worker_inference_assignments").fetchone()[0] == 0
        assert conn.execute(
            "SELECT state,job_id FROM worker_job_safety WHERE worker_id=?", (WORKER,)
        ).fetchone() == ("idle_confirmed", None)


def test_job_without_ready_or_matching_model_cannot_start(tmp_path):
    db, delivery = prepare(tmp_path)
    provider = ArenaPhoneMoveProvider(db, WORKER)
    with pytest.raises(PhoneMoveUnavailable):
        provider(chess.Board(), {"model_sha256": "b" * 64}, None, {})
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT count(*) FROM worker_inference_assignments").fetchone()[0] == 0


def test_early_stop_keeps_assignment_fenced(tmp_path):
    db, delivery = prepare(tmp_path)
    stop = threading.Event()
    stop.set()
    provider = ArenaPhoneMoveProvider(db, WORKER, clock=lambda: 1000.0)
    with pytest.raises(PhoneMoveUnavailable):
        provider(chess.Board(), {"model_sha256": MODEL}, stop, {})
    with sqlite3.connect(db) as conn:
        assert conn.execute(
            "SELECT state FROM worker_job_safety WHERE worker_id=?", (WORKER,)
        ).fetchone()[0] == "running"
    assert delivery.register_local_ready(WORKER, MODEL) == "blocked"


def test_expired_remote_move_leaves_lease_for_reconciliation(tmp_path):
    clock_state = [1000.0]
    clock = lambda: clock_state[0]
    db, _ = prepare(tmp_path, clock=clock)
    provider = ArenaPhoneMoveProvider(
        db, WORKER, clock=clock,
        sleep=lambda amount: clock_state.__setitem__(0, clock_state[0] + amount),
        poll_seconds=0.5, timeout_seconds=2,
    )
    with pytest.raises(PhoneMoveUnavailable):
        provider(chess.Board(), {"model_sha256": MODEL}, None, {})
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT count(*) FROM worker_inference_assignments").fetchone()[0] == 1


def test_arena_finalization_rejects_wrong_uci(tmp_path):
    db, delivery = prepare(tmp_path)
    job = {
        "version": 1, "job_id": "move_1234567890", "fen": chess.STARTING_FEN,
        "model_sha256": MODEL, "max_tokens": 16, "timeout_seconds": 600,
    }
    delivery.assign_from_arena(WORKER, job)
    delivery.submit_inference_result(
        WORKER, {"version": 1, "job_id": job["job_id"], "uci": "e2e4"}
    )
    assert delivery.read_arena_result(WORKER, job["job_id"]) == "e2e4"
    with pytest.raises(ValueError):
        delivery.finalize_arena_result(WORKER, job["job_id"], "d2d4")
    assert delivery.read_arena_result(WORKER, job["job_id"]) == "e2e4"
    assert delivery.finalize_arena_result(
        WORKER, job["job_id"], "e2e4"
    ) == chess.Move.from_uci("e2e4")
    with pytest.raises(ValueError):
        delivery.finalize_arena_result(WORKER, job["job_id"], "e2e4")
