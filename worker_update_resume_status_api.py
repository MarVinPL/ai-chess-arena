"""Machine-token-only resume readiness query, extracted from Worker PR #296.

This read-only request does not grant installation, resume execution,
checkpoint ownership or a lease. Panel cookies are never accepted.
"""
import json
import re

from remote_worker_tokens import WorkerTokenStore
from remote_worker_update_pause import WorkerUpdatePause

ROUTE = "/api/remote-workers/update-resume-status"
_NONCE = re.compile(r"[0-9a-f]{32}\Z")


def handle_update_resume_status(handler, database):
    """Preserve the #296 JSON contract and fail-closed Worker token auth."""
    try:
        length = int(handler.headers.get("Content-Length", "0"))
        if not 0 < length <= 2048:
            handler.send_json({"error": "Nieprawidłowe żądanie"}, 400)
            return
        payload = json.loads(handler.rfile.read(length))
        if not isinstance(payload, dict):
            raise ValueError("Invalid resume status request")
        worker_id, token, nonce = (
            payload.get("worker_id"), payload.get("token"), payload.get("nonce")
        )
        if not isinstance(nonce, str) or not _NONCE.fullmatch(nonce):
            raise ValueError("Invalid resume status nonce")
        if not WorkerTokenStore(database).authenticate(worker_id, token):
            handler.send_json({"error": "Brak autoryzacji"}, 401)
            return
        snapshot = WorkerUpdatePause(database).resume_status(worker_id)
        if snapshot["state"] == "awaiting_local_engine":
            from remote_worker_resume_transport import ResumeTransport
            offer = ResumeTransport(database).discover(worker_id)
            if offer is not None:
                snapshot["delivery_offer"] = offer
        handler.send_json({
            "authenticated": True, "source": "coordinator",
            "worker_id": worker_id, "nonce": nonce,
            "resume_available": False, **snapshot,
        })
    except (ValueError, TypeError, UnicodeError, json.JSONDecodeError):
        handler.send_json({"error": "Nieprawidłowe żądanie wznowienia"}, 400)
    except Exception:
        handler.send_json({"error": "Nie można potwierdzić wznowienia"}, 500)
