"""Browser-authenticated Arena model readiness test history GET (#314).

The HTTP composition root validates the panel session before delegation.
Use the authoritative Arena SQLite DB supplied by server.py; this endpoint
does not authenticate Worker machine tokens or create another database.
"""
import sqlite3
from urllib.parse import parse_qs, urlparse


def dispatch_panel_passkey_tests_get(handler, path, raw_path, database):
    """Preserve the existing path matching, filtering and row order."""
    if not path.startswith("/api/passkey-tests"):
        return False
    tournament_id = parse_qs(urlparse(raw_path).query).get(
        "tournament_id", [None]
    )[0]
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT tournament_id,model_id,model_name,question_no,question,"
            "status,answer,elapsed_seconds,created_at FROM passkey_test_answers"
            + (" WHERE tournament_id=?" if tournament_id else "")
            + " ORDER BY tournament_id,model_name,question_no",
            ((int(tournament_id),) if tournament_id else ()),
        ).fetchall()
    handler.send_json([dict(row) for row in rows])
    return True
