"""Arena owns game state while the Android model computes a legal UCI move."""
import sqlite3
import threading

import chess
import pytest
import tournament_checkpoint

from remote_worker_inference_delivery import WorkerInferenceDelivery
from remote_worker_phone_move_provider import ArenaPhoneMoveProvider, ArenaPhonePoolProvider, PhoneMoveUnavailable

WORKER = "android_1234567890abcdef"
MODEL = "a" * 64


def prepare(tmp_path, *, clock=lambda: 1000.0):
    db = tmp_path / "remote_phone.sqlite"
    delivery = WorkerInferenceDelivery(db, clock=clock)
    assert delivery.register_local_ready(WORKER, MODEL) == "ready"
    return db, delivery


def initialize_arena_checkpoint(db, *, tournament_id=1):
    """Minimal authoritative Arena checkpoint in the SAME coordinator DB."""
    tournament_checkpoint.ensure_schema(db)
    payload = {
        "schema_version": 1, "tournament_id": tournament_id,
        "fen": chess.STARTING_FEN, "moves": [], "move_times": [],
        "pairing": {"id": 42}, "white": {"model_sha256": MODEL},
        "black": {"model_sha256": "b" * 64},
    }
    tournament_checkpoint.save(db, tournament_id, payload)
    return payload


def next_arena_checkpoint(previous, uci, *, seconds=1.0):
    board = chess.Board(previous["fen"])
    move = chess.Move.from_uci(uci)
    assert move in board.legal_moves
    board.push(move)
    record = {
        "uci": uci, "fen": board.fen(),
        "ply": len(previous["moves"]) + 1, "seconds": seconds,
    }
    updated = {**previous, "fen": board.fen(),
               "moves": previous["moves"] + [record],
               "move_times": previous["move_times"] + [seconds]}
    return updated, record


def test_arena_receives_phone_result_and_retains_board_authority(tmp_path):
    db, delivery = prepare(tmp_path)
    checkpoint = initialize_arena_checkpoint(db)
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
    # A received UCI must NOT release the phone before Arena checkpoints it.
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT count(*) FROM worker_inference_results").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM worker_inference_assignments").fetchone()[0] == 1
        assert conn.execute(
            "SELECT state FROM worker_job_safety WHERE worker_id=?", (WORKER,)
        ).fetchone()[0] == "running"
    assert tournament_checkpoint.load(db, 1)["fen"] == chess.STARTING_FEN
    after, record = next_arena_checkpoint(checkpoint, "e2e4")
    provider.commit_checkpoint_for_record(1, after, record)
    assert tournament_checkpoint.load(db, 1)["fen"] == record["fen"]
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
    checkpoint = initialize_arena_checkpoint(db)
    job = {
        "version": 1, "job_id": "move_1234567890", "fen": chess.STARTING_FEN,
        "model_sha256": MODEL, "max_tokens": 16, "timeout_seconds": 600,
    }
    delivery.assign_from_arena(WORKER, job)
    delivery.submit_inference_result(
        WORKER, {"version": 1, "job_id": job["job_id"], "uci": "e2e4"}
    )
    assert delivery.read_arena_result(WORKER, job["job_id"]) == "e2e4"
    after, record = next_arena_checkpoint(checkpoint, "e2e4")
    with pytest.raises(ValueError):
        delivery.finalize_arena_result(
            WORKER, job["job_id"], "d2d4", tournament_id=1, checkpoint=after)
    assert delivery.read_arena_result(WORKER, job["job_id"]) == "e2e4"
    assert delivery.finalize_arena_result(
        WORKER, job["job_id"], "e2e4", tournament_id=1, checkpoint=after
    ) == chess.Move.from_uci("e2e4")
    with pytest.raises(ValueError):
        delivery.finalize_arena_result(
            WORKER, job["job_id"], "e2e4", tournament_id=1, checkpoint=after)


def test_phone_pool_selects_only_ready_matching_model(tmp_path):
    db, delivery = prepare(tmp_path)
    checkpoint = initialize_arena_checkpoint(db)
    events = []

    def immediate(worker_id, job_id, fen, player):
        events.append((worker_id, job_id))
        delivery.submit_inference_result(
            worker_id, {"version": 1, "job_id": job_id, "uci": "g1f3"},
        )

    pool = ArenaPhonePoolProvider(
        db, clock=lambda: 1000.0, sleep=lambda _: None,
        on_assignment=immediate,
    )
    move, metadata = pool(
        chess.Board(), {"model_sha256": MODEL}, threading.Event(), {},
    )
    assert move == chess.Move.from_uci("g1f3")
    assert metadata["runtime"] == "android-local-llama-cpp"
    assert len(events) == 1 and events[0][0] == WORKER
    assert delivery.find_ready_worker(MODEL) is None
    after, record = next_arena_checkpoint(checkpoint, "g1f3")
    pool.commit_checkpoint_for_record(1, after, record)
    assert delivery.find_ready_worker(MODEL) == WORKER
    with pytest.raises(PhoneMoveUnavailable):
        pool(chess.Board(), {"model_sha256": "c" * 64}, None, {})


def test_arena_production_runner_is_phone_opt_in_only_and_keeps_its_checkpoint():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    source = (root / "server.py").read_text(encoding="utf-8")
    runner = (root / "engine_runner.py").read_text(encoding="utf-8")
    assert "os.environ.get('ARENA_PHONE_INFERENCE_ENABLED') == '1'" in source
    assert "phone_move_provider = None" in source
    assert "ArenaPhonePoolProvider(" in source
    assert "move_provider=phone_move_provider" in source
    assert "set_live(" in source and "worker_id=worker_id, job_id=job_id, fen=fen" in source
    assert "tournament_checkpoint.save(DB, tournament_id" in source
    assert "if self.move_provider is None:" in runner


def test_phone_mode_skips_vps_download_but_preserves_local_mode(monkeypatch):
    import server
    local_called = []
    monkeypatch.setattr(
        server, "_prepare_game_models",
        lambda white, black: local_called.append((white, black))
        or ({"path": "/vps/white.gguf", "draft_path": None},
            {"path": "/vps/black.gguf", "draft_path": None}),
    )
    a, b = {"model_sha256": MODEL}, {"model_sha256": "b" * 64}
    remote = server._prepare_game_execution_assets(a, b, phone_inference_enabled=True)
    assert remote == (
        {"path": None, "draft_path": None},
        {"path": None, "draft_path": None},
    )
    assert not local_called
    local = server._prepare_game_execution_assets(a, b, phone_inference_enabled=False)
    assert local[0]["path"] == "/vps/white.gguf"
    assert len(local_called) == 1
    with pytest.raises(ValueError):
        server._prepare_game_execution_assets(
            {"model_sha256": "bad"}, b, phone_inference_enabled=True,
        )


def test_crash_during_checkpoint_commit_rolls_back_lease_and_chess(
    tmp_path, monkeypatch,
):
    db, delivery = prepare(tmp_path)
    previous = initialize_arena_checkpoint(db)
    job = {
        "version": 1, "job_id": "move_crash_test_1",
        "fen": chess.STARTING_FEN, "model_sha256": MODEL,
        "max_tokens": 16, "timeout_seconds": 600,
    }
    delivery.assign_from_arena(WORKER, job)
    delivery.submit_inference_result(
        WORKER, {"version": 1, "job_id": job["job_id"], "uci": "e2e4"}
    )
    upcoming, record = next_arena_checkpoint(previous, "e2e4")
    original = tournament_checkpoint.save_in_transaction

    def fail_after_writing(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("Simulated process failure before SQLite commit")

    with monkeypatch.context() as patch:
        patch.setattr(tournament_checkpoint, "save_in_transaction",
                      fail_after_writing)
        with pytest.raises(RuntimeError):
            delivery.finalize_arena_result(
                WORKER, job["job_id"], "e2e4",
                tournament_id=1, checkpoint=upcoming,
            )
    # Both the new checkpoint and idle transition must roll back.
    assert tournament_checkpoint.load(db, 1) == previous
    with sqlite3.connect(db) as conn:
        assert conn.execute(
            "SELECT state,job_id FROM worker_job_safety WHERE worker_id=?",
            (WORKER,),
        ).fetchone() == ("running", job["job_id"])
        assert conn.execute(
            "SELECT count(*) FROM worker_inference_results WHERE job_id=?",
            (job["job_id"],),
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT count(*) FROM worker_inference_assignments WHERE job_id=?",
            (job["job_id"],),
        ).fetchone()[0] == 1
    # Retrying the identical Arena checkpoint commit works exactly once.
    delivery.finalize_arena_result(
        WORKER, job["job_id"], "e2e4", tournament_id=1, checkpoint=upcoming
    )
    assert tournament_checkpoint.load(db, 1) == upcoming


def test_mismatched_or_skipped_arena_checkpoint_never_releases_phone(tmp_path):
    db, delivery = prepare(tmp_path)
    old = initialize_arena_checkpoint(db)
    job = {
        "version": 1, "job_id": "move_wrong_cp_1",
        "fen": chess.STARTING_FEN, "model_sha256": MODEL,
        "max_tokens": 16, "timeout_seconds": 600,
    }
    delivery.assign_from_arena(WORKER, job)
    delivery.submit_inference_result(
        WORKER, {"version": 1, "job_id": job["job_id"], "uci": "e2e4"}
    )
    good, _record = next_arena_checkpoint(old, "e2e4")
    for bad in (
        {**good, "fen": chess.STARTING_FEN},
        {**good, "moves": []},
        {**good, "pairing": {"id": 99}},
        {**good, "move_times": []},
        {**good, "moves": [{**good["moves"][0], "uci": "d2d4"}]},
    ):
        with pytest.raises(ValueError):
            delivery.finalize_arena_result(
                WORKER, job["job_id"], "e2e4",
                tournament_id=1, checkpoint=bad,
            )
        assert tournament_checkpoint.load(db, 1) == old
        with sqlite3.connect(db) as conn:
            assert conn.execute(
                "SELECT state FROM worker_job_safety WHERE worker_id=?",
                (WORKER,),
            ).fetchone()[0] == "running"


def test_server_publishes_remote_move_via_atomic_checkpoint_commit():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    source = (root / "server.py").read_text(encoding="utf-8")
    delivery = (root / "remote_worker_inference_delivery.py").read_text(
        encoding="utf-8")
    assert "phone_move_provider.commit_checkpoint_for_record(" in source
    assert "tournament_checkpoint.save_in_transaction(" in delivery
    assert "db.execute(\"BEGIN IMMEDIATE\")" in delivery
    assert "worker_inference_results" in delivery
    assert "state='idle_confirmed'" in delivery
