"""Authenticated panel tournament read API (Issue #314).

Only called by the browser-session-checked composition root. The existing
league database, preflight implementation and live snapshot are shared;
no independent tournament scheduler or Worker token handler is created.
"""
import league

TOURNAMENT_GET_LIST = "/api/tournaments"
TOURNAMENT_GET_PREFIX = "/api/tournaments/"


def dispatch_panel_tournament_get(handler, path, *, get_live, tournament_preflight):
    if path == TOURNAMENT_GET_LIST:
        with league.connect() as connection:
            rows = connection.execute("""SELECT t.id,t.name,t.format,t.status,t.created_at,
                (SELECT COUNT(*) FROM tournament_pairings p WHERE p.tournament_id=t.id AND p.stage='league') AS games_planned,
                (SELECT COUNT(*) FROM tournament_pairings p WHERE p.tournament_id=t.id AND p.stage='draft') AS draft_games_played_or_planned,
                (SELECT COUNT(DISTINCT p.id)
                 FROM tournament_pairings p
                 JOIN games g ON g.id=p.game_id
                   AND g.tournament_id=p.tournament_id
                   AND g.pairing_id=p.id
                 WHERE p.tournament_id=t.id
                   AND p.status='completed'
                   AND g.result IN ('1-0','0-1','1/2-1/2')) AS completed_games,
                (SELECT COUNT(*) FROM tournament_entries e WHERE e.tournament_id=t.id) AS players,
                t.qualification_percent,t.resume_blocked,t.initial_entrant_count,t.late_admission_limit,
                t.late_admission_used,t.parent_tournament_id,t.admission_batch_id,
                EXISTS(SELECT 1 FROM tournament_checkpoints cp WHERE cp.tournament_id=t.id) AS has_checkpoint,
                CASE WHEN t.rules_version='2' THEN
                  (SELECT COUNT(*) FROM tournament_entries e WHERE e.tournament_id=t.id) *
                  ((SELECT COUNT(*) FROM tournament_entries e WHERE e.tournament_id=t.id)-1) +
                  MAX(1,COALESCE((SELECT COUNT(*) FROM tournament_seeds q WHERE q.tournament_id=t.id),CAST(((SELECT COUNT(*) FROM tournament_entries e WHERE e.tournament_id=t.id)*t.qualification_percent+99)/100 AS INTEGER)))-1
                ELSE (SELECT COUNT(*) FROM tournament_pairings p WHERE p.tournament_id=t.id) END AS total_games_planned
                FROM tournaments t ORDER BY t.id DESC""").fetchall()
        handler.send_json([dict(row) for row in rows])
        return True

    # Preserve legacy prefix/suffix behavior (including the trailing slash).
    if not path.startswith(TOURNAMENT_GET_PREFIX):
        return False
    if path.endswith("/view"):
        try:
            tournament_id = int(path.split("/")[3])
            handler.send_json(league.tournament_view(tournament_id, get_live()))
        except KeyError as exc:
            handler.send_json({"error": str(exc)}, 404)
        except (ValueError, IndexError):
            handler.send_json({"error": "Nieprawidłowy identyfikator turnieju"}, 422)
        return True
    if path.endswith("/preflight"):
        try:
            tournament_id = int(path.split("/")[3])
            with league.connect() as connection:
                exists = connection.execute(
                    "SELECT 1 FROM tournaments WHERE id=?", (tournament_id,)
                ).fetchone()
            if not exists:
                handler.send_json({"error": "Nie znaleziono turnieju"}, 404)
            else:
                handler.send_json(tournament_preflight(tournament_id))
        except (ValueError, IndexError):
            handler.send_json({"error": "Nieprawidłowy identyfikator turnieju"}, 422)
        return True
    return False
