"""Browser-only game-control routing boundary (Issue #314).

HTTP POST routing is separate from the Worker API. Actual game mutations,
locks, state serialization and autoplay threads remain in the single Arena
composition root; only the authenticated browser-panel handler may call this
adapter. No alternate board, thread or scheduler is created here.
"""

PANEL_GAME_CONTROL_POST_ROUTES = frozenset({
    "/api/step", "/api/run", "/api/stop", "/api/reset",
})


def dispatch_panel_game_control_post(handler, path, *, control_action):
    """Delegate an exact panel control route to the authoritative game state."""
    if path not in PANEL_GAME_CONTROL_POST_ROUTES:
        return False
    control_action(handler, path)
    return True
