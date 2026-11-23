"""Issue #314: browser-authenticated game history/PGN contracts."""
import io
import json
import sqlite3
from pathlib import Path

import web_panel_games_api as api


class Reply:
    def __init__(self):
        self.json = []
        self.errors = []
        self.status = None
        self.headers = {}
        self.wfile = io.BytesIO()

    def send_json(self, value, status=200):
        self.json.append((status, value))

    def send_error(self, status, reason):
        self.errors.append((status, reason))

    def send_response(self, status):
        self.status = status

    def send_header(self, name, value):
        self.headers[name] = value

    def end_headers(self):
        pass


def database(tmp_path):
    db = tmp_path / "arena.sqlite3"
    with sqlite3.connect(db) as connection:
        connection.execute("""CREATE TABLE games (
            id INTEGER PRIMARY KEY, started_at TEXT, ended_at TEXT,
            white_model TEXT, black_model TEXT, result TEXT, pgn TEXT,
            move_count INTEGER, tournament_id INTEGER, pairing_id INTEGER,
            avg_move_seconds REAL, adjudication_status TEXT,
            adjudication_json TEXT, inference_metadata_json TEXT,
            failure_reason TEXT, failure_fen TEXT, failure_color TEXT,
            failure_side_to_move TEXT, failure_model_id INTEGER
        )""")
        metadata = json.dumps([
            {"arbiter_intervention": True, "ply": 4, "color": "white",
             "model": "test", "arbiter_signal": "illegal_move",
             "arbiter_response": "retry", "seconds": 2.5, "uci": "e2e4"},
            {"arbiter_intervention": False, "ply": 5},
        ])
        connection.execute("""INSERT INTO games
            (id,started_at,ended_at,white_model,black_model,result,pgn,
             move_count,tournament_id,pairing_id,avg_move_seconds,
             adjudication_status,adjudication_json,inference_metadata_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (17, "now", "later", "A", "B", "1-0",
             '[Event "Test"]\n1. e4', 1, 3, 5, 2.34567, "ok", "{}", metadata))
    return db


def test_unrecognized_routes_and_worker_api_are_never_intercepted(tmp_path):
    handler = Reply()
    assert api.dispatch_panel_games_get(handler, "/api/remote-workers/game", database(tmp_path)) is False
    assert api.dispatch_panel_games_get(handler, "/api/games-extra", database(tmp_path)) is False
    assert not handler.json and not handler.errors


def test_game_list_preserves_legacy_shape_and_arbiter_count(tmp_path):
    handler = Reply()
    assert api.dispatch_panel_games_get(handler, "/api/games", database(tmp_path))
    status, result = handler.json[0]
    assert status == 200
    game = result[0]
    assert game["white"] == "A" and game["black"] == "B"
    assert game["moves"] == 1
    assert game["avg_seconds"] == 2.346
    assert game["arbiter_interventions"] == 1
    assert "inference_metadata_json" not in game


def test_game_detail_preserves_arbiter_event_data(tmp_path):
    handler = Reply()
    assert api.dispatch_panel_games_get(handler, "/api/games/17", database(tmp_path))
    status, game = handler.json[0]
    assert status == 200 and game["id"] == 17
    assert game["arbiter_events"] == [{
        "ply": 4, "color": "white", "model": "test",
        "signal": "illegal_move", "response": "retry",
        "response_seconds": 2.5, "selected_move": "e2e4",
    }]


def test_pgn_download_preserves_content_type_filename_and_body(tmp_path):
    handler = Reply()
    assert api.dispatch_panel_games_get(handler, "/api/games/17/pgn", database(tmp_path))
    assert handler.status == 200
    assert handler.headers["Content-Type"] == "application/x-chess-pgn; charset=utf-8"
    assert handler.headers["Content-Disposition"] == 'inline; filename="game-17.pgn"'
    assert handler.wfile.getvalue() == b'[Event "Test"]\n1. e4'
    assert int(handler.headers["Content-Length"]) == len(handler.wfile.getvalue())


def test_missing_pgn_and_game_detail_keep_different_404_contracts(tmp_path):
    db = database(tmp_path)
    handler = Reply()
    assert api.dispatch_panel_games_get(handler, "/api/games/18/pgn", db)
    assert handler.errors == [(404, "Nie znaleziono zapisanej partii")]
    handler = Reply()
    assert api.dispatch_panel_games_get(handler, "/api/games/18", db)
    assert handler.json == [(404, {"error": "Nie znaleziono zapisanej partii"})]


def test_invalid_game_ids_return_422(tmp_path):
    db = database(tmp_path)
    for route in ("/api/games/not-an-id", "/api/games/not-an-id/pgn"):
        handler = Reply()
        assert api.dispatch_panel_games_get(handler, route, db)
        assert handler.json == [(422, {"error": "Nieprawidłowy identyfikator partii"})]


def test_panel_session_gate_precedes_games_dispatch():
    server = (Path(__file__).resolve().parents[1] / "server.py").read_text("utf-8")
    get = server.split("    def do_GET(self):", 1)[1].split("    def do_POST(self):", 1)[0]
    assert get.index("if not user:") < get.index("web_panel_games_api.dispatch_panel_games_get(")
    adapter = (Path(__file__).resolve().parents[1] / "web_panel_games_api.py").read_text("utf-8")
    assert "WorkerTokenStore" not in adapter
    assert "arena_auth." not in adapter
