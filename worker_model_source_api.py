"""Read-only model-source metadata for the Worker's current assignment (#314).

Ported from PR #296, not a download capability. Arena owns the model
admission, active lease and approval database. This endpoint cannot grant
assignments, install a model, or select a different SHA on the client's behalf.
"""
import json
import re

from remote_worker_tokens import WorkerTokenStore
from remote_worker_inference_delivery import WorkerInferenceDelivery
from remote_worker_model_registry import (
    WorkerModelRegistryError, resolve_approved_worker_gguf,
)

ROUTE = "/api/remote-workers/model-source"
_NONCE = re.compile(r"[0-9a-f]{32}\Z")


def handle_model_source(handler, database):
    """Reply with approved metadata only for an authenticated live assignment."""
    try:
        length = int(handler.headers.get("Content-Length", "0"))
        if not 0 < length <= 2048:
            handler.send_json({"error": "Nieprawidłowe żądanie"}, 400)
            return
        payload = json.loads(handler.rfile.read(length))
        if not isinstance(payload, dict) or set(payload) != {
            "worker_id", "token", "nonce",
        }:
            raise ValueError("Invalid model metadata request")
        worker_id, token, nonce = (
            payload["worker_id"], payload["token"], payload["nonce"]
        )
        if not isinstance(nonce, str) or not _NONCE.fullmatch(nonce):
            raise ValueError("Invalid metadata nonce")
        if not WorkerTokenStore(database).authenticate(worker_id, token):
            handler.send_json({"error": "Brak autoryzacji"}, 401)
            return
        delivery = WorkerInferenceDelivery(database)
        assignment = delivery.poll_assigned_job(worker_id)
        if assignment.get("state") != "assigned":
            handler.send_json({"error": "Brak aktywnego, ważnego przydziału"}, 409)
            return
        approved = resolve_approved_worker_gguf(
            database, assignment["job"]["model_sha256"]
        )
        # Fail closed if the assignment or lease changes while reading
        # approved metadata. The HTTP response never authorizes installation.
        repeat = delivery.poll_assigned_job(worker_id)
        if repeat != assignment or repeat.get("state") != "assigned":
            handler.send_json({"error": "Przydział uległ zmianie"}, 409)
            return
        handler.send_json({
            "authenticated": True, "source": "coordinator",
            "worker_id": worker_id, "nonce": nonce,
            "state": "approved_metadata_only", "model": approved,
        })
    except WorkerModelRegistryError:
        handler.send_json({"error": "Model nie jest dopuszczony"}, 409)
    except (ValueError, TypeError, UnicodeError, json.JSONDecodeError, KeyError):
        handler.send_json({"error": "Nieprawidłowe żądanie metadanych"}, 400)
    except Exception:
        handler.send_json({"error": "Nie można odczytać rejestru modeli"}, 503)
