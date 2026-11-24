"""Opt-in machine-authenticated Android Worker API gateway.

No worker HTTP operation may assign an inference job, transition a lease to
complete, or create an Arena checkpoint. Only the trusted tournament
coordinator can do so. Live phone inference remains DISABLED independently.
"""
import json
import re

from remote_worker_tokens import WorkerTokenStore

NONCE = re.compile(r"[0-9a-f]{32}\Z")
ROUTES = frozenset({
    "/api/remote-workers/job-poll",
    "/api/remote-workers/ready",
    "/api/remote-workers/job-result",
    "/api/remote-workers/update-pause-poll",
    "/api/remote-workers/board-view",
})
MAX_REQUEST_BYTES = 2048


def handle_phone_request(handler, path, db, live):
    """Handle a recognized phone route; caller must check separate enable flag."""
    if path not in ROUTES:
        return False

    try:
        content_type = handler.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            raise ValueError("Content-Type must be application/json")
        length = int(handler.headers.get("Content-Length", "0"))
        if not 0 < length <= MAX_REQUEST_BYTES:
            raise ValueError("Invalid body length")
        payload = json.loads(handler.rfile.read(length))
        if not isinstance(payload, dict):
            raise ValueError("Invalid payload")
        if set(payload) != (
            {"worker_id", "token", "nonce", "model_sha256"}
            if path.endswith("/ready") else
            {"worker_id", "token", "nonce", "result"}
            if path.endswith("/job-result") else
            {"worker_id", "token", "nonce"}
        ):
            raise ValueError("Unexpected protocol fields")
        worker_id, token, nonce = (
            payload.get("worker_id"), payload.get("token"), payload.get("nonce"),
        )
        if not isinstance(nonce, str) or not NONCE.fullmatch(nonce):
            raise ValueError("Invalid nonce")
    except (UnicodeError, ValueError, TypeError, json.JSONDecodeError):
        handler.send_json({"error": "Nieprawidłowe żądanie Workera"}, 400)
        return True

    if not WorkerTokenStore(db).authenticate(worker_id, token):
        # Never return or echo worker identity/nonce in an unauthorized response.
        handler.send_json({"error": "Brak autoryzacji Workera"}, 401)
        return True

    response = {
        "authenticated": True,
        "source": "coordinator",
        "worker_id": worker_id,
        "nonce": nonce,
    }
    try:
        if path.endswith("/job-poll"):
            from remote_worker_inference_delivery import WorkerInferenceDelivery
            response.update(WorkerInferenceDelivery(db).poll_assigned_job(worker_id))
        elif path.endswith("/ready"):
            from remote_worker_inference_delivery import WorkerInferenceDelivery
            response["state"] = WorkerInferenceDelivery(db).register_local_ready(
                worker_id, payload["model_sha256"],
            )
        elif path.endswith("/job-result"):
            from remote_worker_inference_delivery import WorkerInferenceDelivery
            response.update(WorkerInferenceDelivery(db).submit_inference_result(
                worker_id, payload["result"],
            ))
        elif path.endswith("/update-pause-poll"):
            from remote_worker_update_pause import WorkerUpdatePause
            response["instruction"] = WorkerUpdatePause(db).poll_stop_instruction(worker_id)
            response["install_allowed"] = False
        elif path.endswith("/board-view"):
            from remote_worker_board_view import worker_board_snapshot
            response.update(worker_board_snapshot(db, worker_id, live))
        handler.send_json(response)
    except (ValueError, TypeError, json.JSONDecodeError):
        # A stale/invalid result is a conflict; no lease or checkpoint changed.
        code = 409 if path.endswith("/job-result") else 400
        handler.send_json({"error": "Niedozwolony lub nieaktualny stan Workera"}, code)
    except Exception:
        # Never disclose secrets, private paths, database schemas or tracebacks.
        handler.send_json({"error": "Nie można obsłużyć żądania Workera"}, 500)
    return True
