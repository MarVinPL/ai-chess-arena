"""Authenticated web-panel bulk game exports (Issue #314).

The HTTP entrypoint checks the browser session before invoking this adapter.
Both formats read the authoritative Arena SQLite path supplied by server.py,
not a separate DB or Worker service.
"""
import csv
import io
import sqlite3

EXPORT_GET_ROUTES = frozenset({"/api/export/pgn", "/api/export/csv"})
CSV_COLUMNS = (
    "id", "started_at", "ended_at", "white_model", "black_model", "result",
    "move_count", "illegal_attempts", "avg_move_seconds", "pgn",
)


def dispatch_panel_export_get(handler, path, database):
    """Preserve existing byte-accurate export and HTTP header contracts."""
    if path not in EXPORT_GET_ROUTES:
        return False
    if path == "/api/export/pgn":
        with sqlite3.connect(database) as connection:
            rows = connection.execute(
                "SELECT pgn FROM games ORDER BY id"
            ).fetchall()
        body = (
            "\n\n".join(row[0].strip() for row in rows if row[0].strip())
            + ("\n" if rows else "")
        ).encode("utf-8")
        content_type = "application/x-chess-pgn; charset=utf-8"
        disposition = 'attachment; filename="ai-chess-arena.pgn"'
    else:
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(CSV_COLUMNS)
        with sqlite3.connect(database) as connection:
            for row in connection.execute(
                "SELECT id,started_at,ended_at,white_model,black_model,result,"
                "move_count,illegal_attempts,avg_move_seconds,pgn "
                "FROM games ORDER BY id"
            ):
                writer.writerow(row)
        body = output.getvalue().encode("utf-8-sig")
        content_type = "text/csv; charset=utf-8"
        disposition = 'attachment; filename="ai-chess-arena.csv"'
    handler.send_response(200)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Disposition", disposition)
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)
    return True
