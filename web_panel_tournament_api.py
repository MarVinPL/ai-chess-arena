"""Authenticated web-panel tournament administration (Issue #314).

This is a domain adapter, not an HTTP entrypoint. Only the session- and
Origin-checked panel router may invoke it. It reuses league and the live
tournament controls supplied by server.py; no duplicate state, lease, or DB.
"""
import league

TOURNAMENT_POST_ROUTES = frozenset({
    "/api/tournaments",
    "/api/tournaments/start",
    "/api/tournaments/stop",
    "/api/tournaments/admissions",
    "/api/tournaments/entries",
})


def handle_tournament_admin_post(path, payload, *, start_tournament,
                                 active_thread, stop_event):
    """Preserve existing endpoint semantics and fail closed on unknown paths."""
    if path == "/api/tournaments/start":
        return start_tournament(int(payload.get("tournament_id", 0)))
    if path == "/api/tournaments/admissions":
        if active_thread and active_thread.is_alive():
            raise RuntimeError("Najpierw bezpiecznie zatrzymaj aktywny turniej")
        return league.create_late_admission_batch(
            int(payload.get("parent_tournament_id", 0)), payload.get("model_ids"))
    if path == "/api/tournaments/entries":
        if active_thread and active_thread.is_alive():
            raise RuntimeError("Nie można zmieniać składu podczas aktywnego turnieju")
        return league.add_prestart_tournament_entries(
            int(payload.get("tournament_id", 0)), payload.get("model_ids"))
    if path == "/api/tournaments/stop":
        active = bool(active_thread and active_thread.is_alive())
        if active:
            stop_event.set()
            return {"status": "stop_requested", "checkpoint_required": True}
        return {"status": "not_running"}
    if path == "/api/tournaments":
        return league.create_round_robin(
            payload.get("name", "Tournament"),
            payload.get("format", "PURE_AI"),
            payload.get("qualification_percent", 80),
        )
    raise ValueError("Unknown administrative tournament route")
