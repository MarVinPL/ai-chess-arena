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

AUTH_ROUTE = "/api/remote-workers/authenticate"
WORKER_POST_ROUTES = frozenset(ROUTES) | {AUTH_ROUTE}


def dispatch_worker_post(handler, path, db, live_snapshot):
    """Return True only for a recognized Worker path, including denied calls.

    Dispatch must run BEFORE web-panel login and CSRF checks. Unknown paths
    fall through to web routing, where they are not granted Worker access.
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

    if path not in ROUTES:
        return False
    if os.environ.get("ARENA_PHONE_WORKER_API_ENABLED") != "1":
        handler.send_json({"error": "Android Worker API nie jest jeszcze włączone"}, 503)
        return True
    handle_phone_request(handler, path, db, live_snapshot)
    return True
