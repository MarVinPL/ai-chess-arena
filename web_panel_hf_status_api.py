"""Panel-only HF review, queue and preflight status GET operations (#314).

The HTTP composition root checks the browser session before delegation.
All read paths use existing league and HF services, never Worker tokens.
"""
import json
from urllib.parse import parse_qs, urlparse

import league
import hf_candidate_reviews
import hf_candidate_queue
import hf_candidate_runtime

HF_STATUS_GET_ROUTES = frozenset({
    "/api/hf-candidates/review",
    "/api/hf-candidates/review-queue",
    "/api/hf-candidates/audit-queue",
    "/api/hf-candidates/hardware",
    "/api/hf-candidates/runtime-preflight",
})


def dispatch_panel_hf_status_get(handler, path, raw_path):
    """Preserve response status and error contracts for authenticated reads."""
    if path not in HF_STATUS_GET_ROUTES:
        return False
    try:
        if path == "/api/hf-candidates/review":
            params = parse_qs(urlparse(raw_path).query)
            catalog_id = params.get("catalog_id", [""])[0]
            revision = params.get("revision", [""])[0]
            with league.connect() as connection:
                review = hf_candidate_reviews.get(connection, catalog_id, revision)
            handler.send_json(review if review is not None else {
                "review": None, "workflow_status": "not_started",
            })
        elif path == "/api/hf-candidates/review-queue":
            with league.connect() as connection:
                handler.send_json(hf_candidate_reviews.list_proposals(connection))
        elif path == "/api/hf-candidates/audit-queue":
            handler.send_json(hf_candidate_queue.snapshot())
        elif path == "/api/hf-candidates/hardware":
            handler.send_json(hf_candidate_queue.hardware_snapshot())
        else:
            handler.send_json(hf_candidate_runtime.snapshot())
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        status = 503 if path in {
            "/api/hf-candidates/audit-queue",
            "/api/hf-candidates/hardware",
            "/api/hf-candidates/runtime-preflight",
        } else 422
        handler.send_json({"error": str(exc)}, status)
    return True
