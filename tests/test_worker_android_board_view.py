"""Phone displays only the Arena's board bound to this exact active Worker."""
import sqlite3
import time
from pathlib import Path

from remote_worker_jobs import DurableWorkerJobs
from remote_worker_board_view import worker_board_snapshot

ROOT = Path(__file__).resolve().parents[1]
SERVER = (ROOT / "server.py").read_text(encoding="utf-8")
LIB = (ROOT / "remote_worker_gui/src-tauri/src/lib.rs").read_text(encoding="utf-8")
BRIDGE = (ROOT / "remote_worker_gui/src-tauri/src/android_update_coordinator.rs").read_text(encoding="utf-8")
UI = (ROOT / "remote_worker_gui/public/index.html").read_text(encoding="utf-8")
FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


def prepared(tmp_path):
    path = tmp_path / "registry.sqlite"
    DurableWorkerJobs(path)
    now = time.time()
    with sqlite3.connect(path) as db:
        db.execute(
            "INSERT INTO worker_job_safety VALUES (?,?,?,?,?)",
            ("android_1234567890abcdef", "game_12345678", "running", now + 200, now),
        )
    live = {
        "worker_id": "android_1234567890abcdef",
        "job_id": "game_12345678",
        "stage": "game_running",
        "fen": FEN,
        "white": "Qwen", "black": "Llama", "model": "Qwen",
        "last_move": {"uci": "e2e4"},
        "updated_at": now,
    }
    return path, now, live


def test_valid_fresh_worker_assignment_returns_authoritative_board(tmp_path):
    path, now, live = prepared(tmp_path)
    result = worker_board_snapshot(path, live["worker_id"], live, now=now)
    assert result["active"] is True
    assert result["fen"] == FEN
    assert result["job_id"] == "game_12345678"
    assert result["last_move_uci"] == "e2e4"
    assert result["model"] == "Qwen"


def test_other_workers_global_live_view_must_not_leak(tmp_path):
    path, now, live = prepared(tmp_path)
    for bad in (
        {**live, "worker_id": "android_somebodyelse"},
        {**live, "job_id": "game_somebodyelse"},
        {**live, "updated_at": now-50},
        {**live, "fen": "8/8/8/8/8/8/8/8 w - - 0 1"},
        {**live, "stage": "game_finished"},
        {},
    ):
        result = worker_board_snapshot(path, "android_1234567890abcdef", bad, now=now)
        assert result["active"] is False
        assert "fen" not in result
        assert "job_id" not in result


def test_update_hold_and_pause_hide_board(tmp_path):
    path, now, live = prepared(tmp_path)
    with sqlite3.connect(path) as db:
        db.execute(
            "INSERT INTO worker_update_holds VALUES (?,?,?,?)",
            (live["worker_id"], "a" * 64, now, now + 50),
        )
    view = worker_board_snapshot(path, live["worker_id"], live, now=now)
    assert view == {"active": False, "state": "paused"}


def test_expired_or_inactive_assignment_never_displays_board(tmp_path):
    path, now, live = prepared(tmp_path)
    assert not worker_board_snapshot(path, live["worker_id"], live, now=now + 300)["active"]
    with sqlite3.connect(path) as db:
        db.execute(
            "UPDATE worker_job_safety SET state='unknown' WHERE worker_id=?",
            (live["worker_id"],),
        )
    assert not worker_board_snapshot(path, live["worker_id"], live, now=now)["active"]


def test_live_events_never_inherit_previous_worker_identity():
    # The public Arena live state is shared. Every event must explicitly
    # claim the current worker/job or the private Android board is hidden.
    assert "for identity_key in ('worker_id', 'job_id'):" in SERVER
    assert "live_status.pop(identity_key, None)" in SERVER


def test_endpoint_requires_machine_token_and_matching_nonce():
    gateway = (Path(__file__).resolve().parents[1] / 'remote_worker_phone_api.py').read_text()
    assert 'if path in ROUTES:' in SERVER
    assert 'WorkerTokenStore(db).authenticate(worker_id, token)' in gateway
    assert 'worker_board_snapshot(db, worker_id, live)' in gateway
    assert '"nonce": nonce' in gateway
    assert '401)' in gateway
    assert gateway.index('WorkerTokenStore(db).authenticate') < gateway.index('worker_board_snapshot(db, worker_id, live)')
    assert '"board-view"' in BRIDGE
    assert 'get("nonce").and_then(Value::as_str) != Some(nonce.as_str())' in BRIDGE
    assert 'get("worker_id").and_then(Value::as_str) != Some(worker_id)' in BRIDGE
    assert "async fn get_my_worker_board(" in LIB


def test_ui_only_renders_authenticated_worker_board_without_local_game_logic():
    assert "invoke('get_my_worker_board'" in UI
    assert 'id="myWorkerBoard"' in UI
    assert "renderWorkerArenaBoard(view)" in UI
    assert "document.createElement('span')" in UI
    assert "square.textContent=workerPieceSymbols[row[file]]||''" in UI
    assert "document.hidden" in UI
    assert "new chess.Board(" not in UI
