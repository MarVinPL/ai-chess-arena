"""Authenticated browser-panel game history and PGN read endpoints (#314).

The HTTP composition root checks the administrator's browser session
before dispatch. This module reads the existing Arena SQLite game database,
never a Worker token or separate storage backend.
"""
import json
import sqlite3

GAME_GET_PREFIX = "/api/games/"
GAME_LIST_PATH = "/api/games"


def dispatch_panel_games_get(handler, path, database):
    """Handle legacy game routes and preserve response, error and PGN headers."""
    if path != GAME_LIST_PATH and not path.startswith(GAME_GET_PREFIX):
        return False

    if path == GAME_LIST_PATH:
        with sqlite3.connect(database) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute("""SELECT id,started_at,white_model,black_model,result,move_count,
                avg_move_seconds,adjudication_status,adjudication_json,inference_metadata_json,failure_reason,
                failure_fen,failure_color,failure_side_to_move,failure_model_id
                FROM games ORDER BY id DESC LIMIT 30""").fetchall()
        games = []
        for row in rows:
            item = dict(
                row, white=row["white_model"], black=row["black_model"],
                moves=row["move_count"], avg_seconds=round(row["avg_move_seconds"], 3)
            )
            try:
                move_metadata = json.loads(item.get("inference_metadata_json") or "[]")
            except (TypeError, json.JSONDecodeError):
                move_metadata = []
            item["arbiter_interventions"] = sum(
                1 for move in move_metadata
                if isinstance(move, dict) and move.get("arbiter_intervention")
            ) if isinstance(move_metadata, list) else 0
            item.pop("inference_metadata_json", None)
            games.append(item)
        handler.send_json(games)
        return True

    if path.endswith("/pgn"):
        try:
            game_id = int(path.split("/")[3])
            with sqlite3.connect(database) as connection:
                row = connection.execute(
                    "SELECT pgn FROM games WHERE id=?", (game_id,)
                ).fetchone()
            if row is None:
                handler.send_error(404, "Nie znaleziono zapisanej partii")
            else:
                body = row[0].encode("utf-8")
                handler.send_response(200)
                handler.send_header(
                    "Content-Type", "application/x-chess-pgn; charset=utf-8"
                )
                handler.send_header(
                    "Content-Disposition", f'inline; filename="game-{game_id}.pgn"'
                )
                handler.send_header("Content-Length", str(len(body)))
                handler.end_headers()
                handler.wfile.write(body)
        except ValueError:
            handler.send_json({"error": "Nieprawidłowy identyfikator partii"}, 422)
        return True

    try:
        game_id = int(path.rsplit("/", 1)[1])
        with sqlite3.connect(database) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute("""SELECT id,started_at,ended_at,white_model,black_model,result,pgn,
                move_count,tournament_id,pairing_id,adjudication_status,adjudication_json,
                inference_metadata_json,failure_reason,failure_fen,failure_color,failure_side_to_move,failure_model_id
                FROM games WHERE id=?""", (game_id,)).fetchone()
        if row is None:
            handler.send_json({"error": "Nie znaleziono zapisanej partii"}, 404)
        else:
            payload = dict(row)
            try:
                move_metadata = json.loads(payload.get("inference_metadata_json") or "[]")
            except (TypeError, json.JSONDecodeError):
                move_metadata = []
            payload["arbiter_events"] = [
                {
                    "ply": item.get("ply"), "color": item.get("color"),
                    "model": item.get("model"), "signal": item.get("arbiter_signal"),
                    "response": item.get("arbiter_response"),
                    "response_seconds": item.get("seconds"),
                    "selected_move": item.get("uci"),
                }
                for item in move_metadata if item.get("arbiter_intervention")
            ] if isinstance(move_metadata, list) else []
            handler.send_json(payload)
    except ValueError:
        handler.send_json({"error": "Nieprawidłowy identyfikator partii"}, 422)
    return True
