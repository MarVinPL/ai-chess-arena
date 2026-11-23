"""Authenticated panel read-only Hugging Face download status API (#314).

This module is only called after server.Handler's panel session gate. It
uses the existing shared downloader state, and never accepts a Worker token.
"""
import json
from urllib.parse import parse_qs, urlparse

import hf_candidate_downloads

DOWNLOAD_GET_ROUTES = frozenset({
    "/api/hf-candidates/download-plan",
    "/api/hf-candidates/download-history",
    "/api/hf-candidates/download-events",
    "/api/hf-candidates/download-files",
    "/api/hf-candidates/download-jobs",
})


def _param(raw_path, key):
    return parse_qs(urlparse(raw_path).query).get(key, [""])[0]


def dispatch_panel_download_get(handler, path, raw_path):
    """Return True for every handled route, including errors."""
    if path not in DOWNLOAD_GET_ROUTES:
        return False
    try:
        if path == "/api/hf-candidates/download-plan":
            handler.send_json(hf_candidate_downloads.plan_snapshot())
        elif path == "/api/hf-candidates/download-history":
            handler.send_json({"jobs": hf_candidate_downloads.history()})
        elif path == "/api/hf-candidates/download-events":
            job_id = _param(raw_path, "job_id")
            handler.send_json(
                {"events": hf_candidate_downloads.events(job_id)}
                if job_id else {"error": "Brak job_id"},
                200 if job_id else 422,
            )
        elif path == "/api/hf-candidates/download-files":
            catalog_id = _param(raw_path, "catalog_id")
            handler.send_json(hf_candidate_downloads.list_files(catalog_id))
        else:  # exact /download-jobs route
            job_id = _param(raw_path, "job_id")
            job = (hf_candidate_downloads.get(job_id)
                   if job_id else hf_candidate_downloads.current())
            handler.send_json(
                job if job else {"error": "Nie znaleziono zadania."},
                200 if job else 404,
            )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        handler.send_json({"error": str(exc)}, 422)
    return True
