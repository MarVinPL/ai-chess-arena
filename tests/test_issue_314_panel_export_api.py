"""Regression tests for browser-only PGN/CSV exports (#314)."""
import csv
import io
import sqlite3
from pathlib import Path

import pytest
import web_panel_export_api as api


class Reply:
    def __init__(self):
        self.status = None
        self.headers = {}
        self.wfile = io.BytesIO()

    def send_response(self, status):
        self.status = status

    def send_header(self, name, value):
        self.headers[name] = value

    def end_headers(self):
        pass


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "arena.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("""CREATE TABLE games (
            id INTEGER PRIMARY KEY, started_at TEXT, ended_at TEXT,
            white_model TEXT, black_model TEXT, result TEXT, move_count INTEGER,
            illegal_attempts INTEGER, avg_move_seconds REAL, pgn TEXT
        )""")
        connection.executemany("""INSERT INTO games VALUES (?,?,?,?,?,?,?,?,?,?)""", [
            (2, "s2", "e2", "Żubr", "B", "0-1", 2, 0, 1.5, " 1. d4   "),
            (1, "s1", "e1", "A", "B", "1-0", 1, 1, 2.5, " 1. e4 "),
        ])
    return path


def test_unknown_paths_do_not_intercept_worker_or_similar_paths(db):
    reply = Reply()
    assert not api.dispatch_panel_export_get(reply, "/api/remote-workers/export", db)
    assert not api.dispatch_panel_export_get(reply, "/api/export/pgn-extra", db)
    assert reply.status is None and reply.wfile.getvalue() == b""


def test_pgn_bulk_export_keeps_sorting_delimiters_and_headers(db):
    reply = Reply()
    assert api.dispatch_panel_export_get(reply, "/api/export/pgn", db)
    assert reply.status == 200
    assert reply.wfile.getvalue() == b"1. e4\n\n1. d4\n"
    assert reply.headers["Content-Type"] == "application/x-chess-pgn; charset=utf-8"
    assert reply.headers["Content-Disposition"] == 'attachment; filename="ai-chess-arena.pgn"'
    assert int(reply.headers["Content-Length"]) == len(reply.wfile.getvalue())


def test_csv_export_keeps_bom_column_order_and_utf8(db):
    reply = Reply()
    assert api.dispatch_panel_export_get(reply, "/api/export/csv", db)
    assert reply.status == 200
    body = reply.wfile.getvalue()
    assert body.startswith(b"\xef\xbb\xbf")
    rows = list(csv.reader(io.StringIO(body.decode("utf-8-sig"))))
    assert rows[0] == list(api.CSV_COLUMNS)
    assert rows[1][0] == "1"
    assert rows[2][0] == "2"
    assert rows[2][3] == "Żubr"
    assert reply.headers["Content-Type"] == "text/csv; charset=utf-8"
    assert reply.headers["Content-Disposition"] == 'attachment; filename="ai-chess-arena.csv"'
    assert int(reply.headers["Content-Length"]) == len(body)


def test_empty_database_retains_legacy_pgn_and_csv_contracts(tmp_path):
    path = tmp_path / "empty.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("""CREATE TABLE games (
            id INTEGER, started_at TEXT, ended_at TEXT, white_model TEXT,
            black_model TEXT, result TEXT, move_count INTEGER,
            illegal_attempts INTEGER, avg_move_seconds REAL, pgn TEXT
        )""")
    reply = Reply()
    assert api.dispatch_panel_export_get(reply, "/api/export/pgn", path)
    assert reply.wfile.getvalue() == b""
    reply = Reply()
    assert api.dispatch_panel_export_get(reply, "/api/export/csv", path)
    assert reply.wfile.getvalue().startswith(b"\xef\xbb\xbf" + b"id,")


def test_browser_session_gate_precedes_export_dispatch():
    root = Path(__file__).resolve().parents[1]
    server = (root / "server.py").read_text(encoding="utf-8")
    get = server.split("    def do_GET(self):", 1)[1].split("    def do_POST(self):", 1)[0]
    assert get.index("if not user:") < get.index("web_panel_export_api.dispatch_panel_export_get(")
    module = (root / "web_panel_export_api.py").read_text(encoding="utf-8")
    assert "WorkerTokenStore" not in module
    assert "arena_auth." not in module
