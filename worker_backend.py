"""Machine-token Worker HTTP boundary (Issue #314).

Only this router owns /api/remote-workers/* endpoints. The panel's cookie
sessions are not recognized here; all Worker requests must authenticate
through WorkerTokenStore in the underlying API handlers. The existing core
modules and SQLite database remain authoritative.
"""
import json
import os

from remote_worker_phone_api import ROUTES, handle_phone_request
from remote_worker_tokens import WorkerTokenStore
from worker_update_resume_status_api import ROUTE as RESUME_STATUS_ROUTE, handle_update_resume_status

WORKER_ROUTE_PREFIX = "/api/remote-workers/"
AUTH_ROUTE = "/api/remote-workers/authenticate"
WORKER_POST_ROUTES = frozenset(ROUTES) | {AUTH_ROUTE, RESUME_STATUS_ROUTE}


def dispatch_worker_post(handler, path, db, live_snapshot):
    """Own the entire Worker namespace before any panel session logic.

    Known routes use machine-token authentication; unknown Worker paths are
    rejected here rather than falling through to browser-cookie auth.
    """
    if path == AUTH_ROUTE:
        try:
            length = int(handler.headers.get("Content-Length", "0"))
            if not 0 < length <= 2048:
                handler.send_json({"error": "Nieprawidłowe żądanie"}, 400)
                return True
            payload = json.loads(handler.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("Invalid payload")
            worker_id, token = payload.get("worker_id"), payload.get("token")
            if not WorkerTokenStore(db).authenticate(worker_id, token):
                handler.send_json({"authenticated": False}, 401)
                return True
            handler.send_json({"authenticated": True, "worker_id": worker_id})
        except (ValueError, UnicodeError, TypeError, json.JSONDecodeError):
            handler.send_json({"error": "Nieprawidłowe żądanie"}, 400)
        except Exception:
            handler.send_json({"error": "Błąd uwierzytelniania"}, 500)
        return True

    if path == RESUME_STATUS_ROUTE:
        handle_update_resume_status(handler, db)
        return True

    if path not in ROUTES:
        if path.startswith(WORKER_ROUTE_PREFIX):
            handler.send_json({"error": "Nieznana operacja Workera"}, 404)
            return True
        return False
    if os.environ.get("ARENA_PHONE_WORKER_API_ENABLED") != "1":
        handler.send_json({"error": "Android Worker API nie jest jeszcze włączone"}, 503)
        return True
    handle_phone_request(handler, path, db, live_snapshot)
    return True
