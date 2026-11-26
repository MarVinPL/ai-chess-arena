"""Issue #314: panel-only Passkey model readiness history contract."""
import sqlite3
from pathlib import Path
from unittest.mock import Mock

import pytest

import web_panel_passkey_tests_api as api


class Reply:
    def __init__(self):
        self.sent = []

    def send_json(self, payload, status=200):
        self.sent.append((status, payload))


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "arena.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("""CREATE TABLE passkey_test_answers (
            tournament_id INTEGER, model_id INTEGER, model_name TEXT,
            question_no INTEGER, question TEXT, status TEXT, answer TEXT,
            elapsed_seconds REAL, created_at TEXT
        )""")
        connection.executemany("""INSERT INTO passkey_test_answers
            VALUES (?,?,?,?,?,?,?,?,?)""", [
            (2, 5, "Beta", 2, "2+2=?", "passed", "4", 2.1, "2026-11-23"),
            (1, 3, "Alpha", 1, "2+2=?", "passed", "4", 0.5, "2026-11-22"),
            (2, 5, "Beta", 1, "2+2=?", "passed", "4", 1.0, "2026-11-23"),
        ])
    return path


def test_unrelated_and_worker_get_routes_are_not_handled(db):
    reply = Reply()
    for path in ("/api/remote-workers/passkey-tests", "/api/models", "/api/passkey"):
        assert api.dispatch_panel_passkey_tests_get(reply, path, path, db) is False
    assert reply.sent == []


def test_list_returns_same_columns_and_order(db):
    reply = Reply()
    assert api.dispatch_panel_passkey_tests_get(
        reply, "/api/passkey-tests", "/api/passkey-tests", db)
    status, rows = reply.sent[0]
    assert status == 200
    assert [r["tournament_id"] for r in rows] == [1, 2, 2]
    assert [r["question_no"] for r in rows] == [1, 1, 2]
    assert set(rows[0]) == {
        "tournament_id", "model_id", "model_name", "question_no",
        "question", "status", "answer", "elapsed_seconds", "created_at"
    }


def test_tournament_id_filter_is_still_supported(db):
    reply = Reply()
    assert api.dispatch_panel_passkey_tests_get(
        reply, "/api/passkey-tests",
        "/api/passkey-tests?tournament_id=2", db)
    assert [row["question_no"] for row in reply.sent[0][1]] == [1, 2]


def test_url_encoded_tournament_filter_and_empty_filter(db):
    reply = Reply()
    assert api.dispatch_panel_passkey_tests_get(
        reply, "/api/passkey-tests",
        "/api/passkey-tests?tournament_id=%32", db)
    assert len(reply.sent[0][1]) == 2
    reply = Reply()
    assert api.dispatch_panel_passkey_tests_get(
        reply, "/api/passkey-tests",
        "/api/passkey-tests?tournament_id=", db)
    assert len(reply.sent[0][1]) == 3


def test_invalid_tournament_id_preserves_legacy_conversion_error(db):
    reply = Reply()
    with pytest.raises(ValueError):
        api.dispatch_panel_passkey_tests_get(
            reply, "/api/passkey-tests",
            "/api/passkey-tests?tournament_id=not-a-number", db)
    assert reply.sent == []


def test_browser_session_gate_precedes_passkey_dispatch():
    root = Path(__file__).resolve().parents[1]
    server = (root / "server.py").read_text(encoding="utf-8")
    get = server.split("    def do_GET(self):", 1)[1].split("    def do_POST(self):", 1)[0]
    assert get.index("if not user:") < get.index(
        "web_panel_passkey_tests_api.dispatch_panel_passkey_tests_get(")
    module = (root / "web_panel_passkey_tests_api.py").read_text(encoding="utf-8")
    assert "WorkerTokenStore" not in module
    assert "arena_auth." not in module
