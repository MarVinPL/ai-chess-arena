"""Authenticated Worker update-reservation transactions extracted from PR #296.

Reservations are capabilities journaled by the Android client before sending
the request. Only the coordinator's durable registry may mutate holds and
job safety state; an HTTP success never grants APK install permission.
"""
import json
import re
import time

from remote_worker_jobs import DurableWorkerJobs
from remote_worker_tokens import WorkerTokenStore

UPDATE_RESERVE_ROUTE = "/api/remote-workers/update-reserve"
UPDATE_RELEASE_ROUTE = "/api/remote-workers/update-release"
UPDATE_HOLD_STATUS_ROUTE = "/api/remote-workers/update-hold-status"
ROUTES = frozenset({
    UPDATE_RESERVE_ROUTE, UPDATE_RELEASE_ROUTE, UPDATE_HOLD_STATUS_ROUTE,
})
_NONCE = re.compile(r"[0-9a-f]{32}\Z")
_HOLD_TOKEN = re.compile(r"[0-9a-f]{64}\Z")


def handle_worker_update_hold(handler, path, database):
    """Machine-token-only operations; preserve #296 status and payload contract."""
    if path not in ROUTES:
        return False
    try:
        length = int(handler.headers.get("Content-Length", "0"))
        if not 0 < length <= 2048:
            handler.send_json({"error": "Nieprawidłowe żądanie"}, 400)
            return True
        payload = json.loads(handler.rfile.read(length))
        if not isinstance(payload, dict):
            raise ValueError("Invalid payload")
        worker_id, token, nonce = (
            payload.get("worker_id"), payload.get("token"), payload.get("nonce"),
        )
        if not isinstance(nonce, str) or not _NONCE.fullmatch(nonce):
            raise ValueError("Invalid update transaction nonce")
        if not WorkerTokenStore(database).authenticate(worker_id, token):
            handler.send_json({"error": "Brak autoryzacji"}, 401)
            return True
        hold_token = payload.get("hold_token")
        # The pre-journaled client capability is required for all three
        # operations. Never generate an unrecoverable server-only token.
        if not isinstance(hold_token, str) or not _HOLD_TOKEN.fullmatch(hold_token):
            raise ValueError("Invalid or absent journaled hold token")
        registry = DurableWorkerJobs(database)
        response = {
            "authenticated": True, "source": "coordinator",
            "worker_id": worker_id, "nonce": nonce,
            "install_allowed": False,
        }
        if path == UPDATE_RESERVE_ROUTE:
            reserved = registry.reserve_update(worker_id, hold_token=hold_token)
            handler.send_json({
                **response, "state": "reserved", "hold_token": reserved,
                "expires_at_unix_ms": int((time.time() + 300) * 1000),
            })
        elif path == UPDATE_HOLD_STATUS_ROUTE:
            state = registry.inspect_update_hold(worker_id, hold_token)
            handler.send_json({**response, "state": state})
        else:
            registry.release_update_hold(worker_id, hold_token)
            handler.send_json({**response, "state": "unknown", "released": True})
    except (ValueError, TypeError, UnicodeError, json.JSONDecodeError):
        handler.send_json({"error": "Nieprawidłowy stan rezerwacji lub żądanie"}, 409)
    except Exception:
        handler.send_json({"error": "Nie można zmienić rezerwacji aktualizacji"}, 500)
    return True
