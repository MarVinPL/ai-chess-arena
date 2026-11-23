"""Coordinator-only E2E: simulated phone UCI -> Arena game -> durable checkpoint.

No phone runtime or network is mocked as successful: this is deliberately a
server-side integration test, not evidence of an actual Android installation.
"""
import sqlite3
import threading

import chess
import pytest

import engine_runner
import tournament_checkpoint
from remote_worker_inference_delivery import WorkerInferenceDelivery
from remote_worker_phone_move_provider import ArenaPhonePoolProvider

WORKER = "android_1234567890abcdef"
MODEL = "a" * 64
TOURNAMENT = 42


class NoVpsInference:
    def generate(self, *args, **kwargs):
        raise AssertionError("Phone-mode game must not compute on VPS")


def initial_state(db):
    tournament_checkpoint.ensure_schema(db)
    state = {
        "schema_version": 1,
        "tournament_id": TOURNAMENT,
        "fen": chess.STARTING_FEN,
        "side_to_move": "white",
        "moves": [],
        "move_times": [],
        "pairing": {"id": 42},
        "white": {"model_sha256": MODEL},
        "black": {"model_sha256": MODEL},
    }
    tournament_checkpoint.save(db, TOURNAMENT, state)
    return state


def play_simulated_phone_move(db, before, *, inject_failure=False, monkeypatch=None):
    worker = WorkerInferenceDelivery(db, clock=lambda: 1000.0)
    assert worker.register_local_ready(WORKER, MODEL) == "ready"
    events = []
    stop = threading.Event()

    def simulated_phone(worker_id, job_id, fen, player):
        # Test-only phone result, not real Termux / llama.cpp computation.
        if events:
            return
        events.append((worker_id, job_id, fen))
        assert fen == before["fen"]
        worker.submit_inference_result(
            worker_id, {"version": 1, "job_id": job_id, "uci": "e2e4"},
        )

    provider = ArenaPhonePoolProvider(
        db, clock=lambda: 1000.0, sleep=lambda _amount: None,
        on_assignment=simulated_phone,
    )
    def checkpoint_from_arena(record):
        payload = {
            **before,
            "fen": record["fen"],
            "side_to_move": "black",
            "moves": before["moves"] + [record],
            "move_times": before["move_times"] + [record["seconds"]],
        }
        provider.commit_checkpoint_for_record(TOURNAMENT, payload, record)
        stop.set()

    white = {
        "name": "Simulated Phone White", "model_id": 1,
        "model_sha256": MODEL, "inference": {"max_tokens": 16},
    }
    black = {
        "name": "Simulated Phone Black", "model_id": 2,
        "model_sha256": MODEL, "inference": {"max_tokens": 16},
    }
    result = engine_runner.SequentialChessRunner(
        NoVpsInference(), move_provider=provider,
    ).play_game(white, black, stop, progress=checkpoint_from_arena)
    return result, events


def test_one_actual_engine_runner_move_has_single_atomic_arena_checkpoint(tmp_path):
    db = tmp_path / "arena.sqlite"
    original = initial_state(db)
    result, events = play_simulated_phone_move(db, original)
    assert result["status"] == "stopped"
    assert len(result["moves"]) == 1
    assert result["moves"][0]["uci"] == "e2e4"
    assert len(events) == 1
    assert events[0][0] == WORKER
    checkpoint = tournament_checkpoint.load(db, TOURNAMENT)
    assert checkpoint["fen"] == result["fen"]
    assert checkpoint["moves"][0]["uci"] == "e2e4"
    assert len(checkpoint["move_times"]) == 1
    with sqlite3.connect(db) as conn:
        assert conn.execute(
            "SELECT state,job_id FROM worker_job_safety WHERE worker_id=?",
            (WORKER,),
        ).fetchone() == ("idle_confirmed", None)
        assert conn.execute(
            "SELECT COUNT(*) FROM worker_inference_results"
        ).fetchone()[0] == 0


def test_engine_runner_failure_to_write_arena_checkpoint_keeps_phone_fenced(
    tmp_path, monkeypatch,
):
    db = tmp_path / "arena.sqlite"
    original = initial_state(db)
    with monkeypatch.context() as patch:
        def fail_checkpoint(*args, **kwargs):
            raise OSError("simulated Arena storage failure")
        patch.setattr(tournament_checkpoint, "save_in_transaction", fail_checkpoint)
        with pytest.raises(OSError):
            play_simulated_phone_move(db, original)
    assert tournament_checkpoint.load(db, TOURNAMENT) == original
    with sqlite3.connect(db) as conn:
        assert conn.execute(
            "SELECT state FROM worker_job_safety WHERE worker_id=?", (WORKER,)
        ).fetchone()[0] == "running"
        assert conn.execute(
            "SELECT COUNT(*) FROM worker_inference_results"
        ).fetchone()[0] == 1
