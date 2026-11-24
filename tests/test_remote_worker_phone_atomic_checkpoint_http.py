"""Gateway -> Arena-owned UCI validation -> atomic checkpoint/lease E2E.

Loopback-only HTTP: ephemeral SQLite DB and test Worker token. This simulates
the Android protocol, not real GGUF inference on physical Android hardware.
"""
import json
import sqlite3
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import chess
import pytest

import server
import tournament_checkpoint
from remote_worker_inference_delivery import WorkerInferenceDelivery
from remote_worker_tokens import WorkerTokenStore

WORKER = "android_test_12345678"
MODEL = "a" * 64
NONCE = "0123456789abcdef0123456789abcdef"


@pytest.fixture()
def game_http(tmp_path, monkeypatch):
    db = tmp_path / "arena.sqlite3"
    with sqlite3.connect(db) as c:
        c.execute("CREATE TABLE tournaments (id INTEGER PRIMARY KEY)")
        c.execute("INSERT INTO tournaments(id) VALUES (1)")
    tournament_checkpoint.ensure_schema(db)
    start = {
        "schema_version": tournament_checkpoint.SCHEMA_VERSION,
        "tournament_id": 1,
        "phase": "game",
        "pairing": {"id": 7},
        "white": {"name": "white_test"},
        "black": {"name": "black_test"},
        "moves": [],
        "move_times": [],
        "fen": chess.STARTING_FEN,
        "side_to_move": "white",
    }
    tournament_checkpoint.save(db, 1, start)
    token = WorkerTokenStore(db).issue(WORKER, ttl_seconds=600)
    delivery = WorkerInferenceDelivery(db)
    monkeypatch.setattr(server, "DB", db)
    monkeypatch.setenv("ARENA_PHONE_WORKER_API_ENABLED", "1")
    monkeypatch.setenv("ARENA_PHONE_INFERENCE_ENABLED", "0")
    service = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    thread = threading.Thread(target=service.serve_forever, daemon=True)
    thread.start()

    def post(route, **fields):
        payload = {
            "worker_id": WORKER, "token": token, "nonce": NONCE,
            **fields,
        }
        req = urllib.request.Request(
            f"http://127.0.0.1:{service.server_port}/api/remote-workers/{route}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=4) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as exc:
            return exc.code, json.load(exc)

    try:
        yield db, delivery, post
    finally:
        service.shutdown()
        service.server_close()
        thread.join(timeout=3)


def job_for(checkpoint, name):
    board = chess.Board(checkpoint["fen"])
    return {
        "version": 2,
        "job_id": name,
        "fen": checkpoint["fen"],
        "model_sha256": MODEL,
        "max_tokens": 16,
        "timeout_seconds": 120,
        "legal_moves": sorted(move.uci() for move in board.legal_moves),
    }


def next_checkpoint(predecessor, uci):
    board = chess.Board(predecessor["fen"])
    board.push_uci(uci)
    record = {
        "uci": uci,
        "ply": len(predecessor["moves"]) + 1,
        "color": "white" if len(predecessor["moves"]) % 2 == 0 else "black",
        "fen": board.fen(),
        "seconds": 0.1,
    }
    return {
        **predecessor,
        "moves": predecessor["moves"] + [record],
        "move_times": predecessor["move_times"] + [0.1],
        "fen": board.fen(),
        "side_to_move": "white" if board.turn else "black",
    }


def setup_assignment(db, delivery, post, *, job_id="move_test_12345678"):
    code, ready = post("ready", model_sha256=MODEL)
    assert code == 200 and ready["state"] == "ready"
    before = tournament_checkpoint.load(db, 1)
    job = job_for(before, job_id)
    assert delivery.assign_from_arena(WORKER, job) == job_id
    code, assignment = post("job-poll")
    assert code == 200 and assignment["state"] == "assigned"
    assert assignment["job"] == job
    return before, job


def test_two_moves_http_to_authoritative_checkpoint_and_idle(game_http):
    db, delivery, post = game_http
    for index, move in enumerate(("e2e4", "e7e5"), start=1):
        old, job = setup_assignment(
            db, delivery, post, job_id=f"move_test_1234567{index}"
        )
        # A phone has no authority to move the pieces on Arena.
        code, rejection = post("job-result", result={
            "version": 1, "job_id": job["job_id"], "uci": "e2e5"
            if index == 1 else "e2e4",
        })
        assert code == 409 and "nonce" not in rejection
        assert tournament_checkpoint.load(db, 1) == old

        code, accepted = post("job-result", result={
            "version": 1, "job_id": job["job_id"], "uci": move,
        })
        assert code == 200
        assert accepted["state"] == "received_not_applied"
        code, repeated = post("job-result", result={
            "version": 1, "job_id": job["job_id"], "uci": move,
        })
        assert code == 200 and repeated["state"] == "already_received"
        assert tournament_checkpoint.load(db, 1) == old

        code, awaiting = post("job-poll")
        assert code == 200 and awaiting["state"] == "awaiting_arena"
        assert "job" not in awaiting
        assert delivery.read_arena_result(WORKER, job["job_id"]) == move
        with sqlite3.connect(db) as c:
            assert c.execute(
                "SELECT state,job_id FROM worker_job_safety WHERE worker_id=?",
                (WORKER,),
            ).fetchone() == ("running", job["job_id"])

        next_one = next_checkpoint(old, move)
        finalized = delivery.finalize_arena_result(
            WORKER, job["job_id"], move,
            tournament_id=1, checkpoint=next_one,
        )
        assert finalized.uci() == move
        assert tournament_checkpoint.load(db, 1) == next_one
        with sqlite3.connect(db) as c:
            assert c.execute(
                "SELECT state,job_id FROM worker_job_safety WHERE worker_id=?",
                (WORKER,),
            ).fetchone() == ("idle_confirmed", None)
        assert post("job-poll")[1]["state"] == "none"
        assert post("job-result", result={
            "version": 1, "job_id": job["job_id"], "uci": move,
        })[0] == 409
        with pytest.raises(ValueError):
            delivery.finalize_arena_result(
                WORKER, job["job_id"], move,
                tournament_id=1, checkpoint=next_one,
            )

    final = tournament_checkpoint.load(db, 1)
    assert [m["uci"] for m in final["moves"]] == ["e2e4", "e7e5"]
    expected = chess.Board()
    expected.push_uci("e2e4")
    expected.push_uci("e7e5")
    assert final["fen"] == expected.fen()


def test_failure_mid_commit_rolls_back_checkpoint_and_leaves_running(game_http, monkeypatch):
    db, delivery, post = game_http
    old, job = setup_assignment(db, delivery, post)
    assert post("job-result", result={
        "version": 1, "job_id": job["job_id"], "uci": "e2e4",
    })[0] == 200

    original = tournament_checkpoint.save_in_transaction
    def fail_after_checkpoint_write(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("simulated crash after checkpoint update")
    monkeypatch.setattr(tournament_checkpoint, "save_in_transaction",
                        fail_after_checkpoint_write)

    with pytest.raises(RuntimeError, match="simulated crash"):
        delivery.finalize_arena_result(
            WORKER, job["job_id"], "e2e4",
            tournament_id=1, checkpoint=next_checkpoint(old, "e2e4"),
        )
    assert tournament_checkpoint.load(db, 1) == old
    assert delivery.read_arena_result(WORKER, job["job_id"]) == "e2e4"
    assert post("job-poll")[1]["state"] == "awaiting_arena"
    with sqlite3.connect(db) as c:
        assert c.execute(
            "SELECT state,job_id FROM worker_job_safety WHERE worker_id=?",
            (WORKER,),
        ).fetchone() == ("running", job["job_id"])


def test_changed_checkpoint_fails_cas_without_idle_transition(game_http):
    db, delivery, post = game_http
    old, job = setup_assignment(db, delivery, post)
    assert post("job-result", result={
        "version": 1, "job_id": job["job_id"], "uci": "e2e4",
    })[0] == 200
    # Another trusted Arena writer changed the predecessor since dispatch.
    tournament_checkpoint.save(db, 1, {**old, "phase": "changed"})
    with pytest.raises(ValueError, match="checkpoint"):
        delivery.finalize_arena_result(
            WORKER, job["job_id"], "e2e4",
            tournament_id=1, checkpoint=next_checkpoint(old, "e2e4"),
        )
    assert tournament_checkpoint.load(db, 1)["phase"] == "changed"
    assert delivery.read_arena_result(WORKER, job["job_id"]) == "e2e4"
    assert post("job-poll")[1]["state"] == "awaiting_arena"


def test_cas_requires_caller_owned_transaction(game_http):
    db, _, _ = game_http
    original = tournament_checkpoint.load(db, 1)
    with sqlite3.connect(db) as c:
        row = c.execute(
            "SELECT sha256 FROM tournament_checkpoints WHERE tournament_id=1"
        ).fetchone()
        with pytest.raises(ValueError, match="transaction"):
            tournament_checkpoint.save_in_transaction(
                c, 1, next_checkpoint(original, "e2e4"), expected_sha256=row[0],
            )
        c.execute("BEGIN IMMEDIATE")
        with pytest.raises(ValueError, match="checkpoint hash"):
            tournament_checkpoint.save_in_transaction(
                c, 1, next_checkpoint(original, "e2e4"),
                expected_sha256="Z" * 64,
            )
        c.rollback()
    assert tournament_checkpoint.load(db, 1) == original


def test_wrong_side_to_move_rejected_without_checkpoint_commit(game_http):
    db, delivery, post = game_http
    old, job = setup_assignment(db, delivery, post)
    assert post("job-result", result={
        "version": 1, "job_id": job["job_id"], "uci": "e2e4"
    })[0] == 200
    invalid = {**next_checkpoint(old, "e2e4"), "side_to_move": "white"}
    with pytest.raises(ValueError, match="does not extend"):
        delivery.finalize_arena_result(
            WORKER, job["job_id"], "e2e4",
            tournament_id=1, checkpoint=invalid,
        )
    assert tournament_checkpoint.load(db, 1) == old
    assert delivery.read_arena_result(WORKER, job["job_id"]) == "e2e4"
