"""Authenticated panel live match state reads (Issue #314).

This adapter owns only HTTP route dispatch. The active chess board, locks,
current match and live snapshot remain exclusively inside server.py. The
HTTP entrypoint performs browser-session authentication before delegation;
Worker machine tokens never authenticate these views.
"""

STATE_GET_ROUTES = frozenset({"/api/state", "/api/live"})


def dispatch_panel_live_state_get(handler, path, *, state_snapshot, live_snapshot):
    """Send the existing snapshot objects without cloning live game state."""
    if path not in STATE_GET_ROUTES:
        return False
    if path == "/api/state":
        handler.send_json(state_snapshot())
    else:
        handler.send_json(live_snapshot())
    return True
