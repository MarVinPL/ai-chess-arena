"""Read-only Worker update safety snapshot (Issue #314, ported from PR #296).

Machine token and nonce are mandatory; the coordinator's durable Worker DB
is the only source of evidence. This response NEVER authorizes installation,
acquires an update hold, or marks a lease finished.
"""
import json
import re
import time

from remote_worker_jobs import DurableWorkerJobs
from remote_worker_tokens import WorkerTokenStore

ROUTE = "/api/remote-workers/update-safety"
_NONCE = re.compile(r"[0-9a-f]{32}\Z")


def handle_update_safety(handler, database):
    """Report current durable worker safety evidence without changing it."""
    try:
        length = int(handler.headers.get("Content-Length", "0"))
        if not 0 < length <= 2048:
            handler.send_json({"error": "Nieprawidłowe żądanie"}, 400)
            return
        payload = json.loads(handler.rfile.read(length))
        if not isinstance(payload, dict):
            raise ValueError("Invalid update safety request")
        worker_id, token, nonce = (
            payload.get("worker_id"), payload.get("token"), payload.get("nonce")
        )
        if not isinstance(nonce, str) or not _NONCE.fullmatch(nonce):
            raise ValueError("Invalid update-safety nonce")
        if not WorkerTokenStore(database).authenticate(worker_id, token):
            handler.send_json({"error": "Brak autoryzacji"}, 401)
            return
        state, active_tasks = DurableWorkerJobs(database).update_safety_snapshot(worker_id)
        handler.send_json({
            "state": state,
            "authenticated": True,
            "source": "coordinator",
            "worker_id": worker_id,
            "nonce": nonce,
            "active_tasks": active_tasks,
            "checked_at_unix_ms": int(time.time() * 1000),
            "install_allowed": False,
            "reason": (
                "Instalacja wymaga niezależnej weryfikacji podpisu, wersji "
                "i ponownego stanu zadań"
            ),
        })
    except (ValueError, UnicodeError, TypeError, json.JSONDecodeError):
        handler.send_json({"error": "Nieprawidłowe żądanie"}, 400)
    except Exception:
        handler.send_json({"error": "Nie można potwierdzić stanu zadań"}, 500)
