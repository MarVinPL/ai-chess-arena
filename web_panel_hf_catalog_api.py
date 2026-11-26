"""Authenticated browser-panel HF catalog and stored audit GET endpoints (#314).

These reads are routed only after the panel's browser-session check; they
reuse the Arena catalog and league connection, not Worker machine tokens.
"""
import json
from urllib.parse import parse_qs, urlparse

import hf_candidate_audit
import hf_candidate_reviews
import league

HF_CATALOG_GET_ROUTES = frozenset({
    "/api/hf-candidates/audit",
    "/api/hf-candidates",
})

CANDIDATE_FIELDS = (
    "catalog_id", "arena_model_id", "display_name", "repo_id", "revision",
    "audited_revision", "declared_license_tag", "gated", "source_status",
    "metadata_status", "license_review_status", "technical_readiness",
    "admission_status",
)


def dispatch_panel_hf_catalog_get(handler, path, raw_path):
    """Preserve existing payload shapes and error status policies."""
    if path not in HF_CATALOG_GET_ROUTES:
        return False
    if path == "/api/hf-candidates/audit":
        try:
            params = parse_qs(urlparse(raw_path).query)
            catalog_id = params.get("catalog_id", [""])[0]
            revision = params.get("revision", [""])[0]
            with league.connect() as connection:
                report = hf_candidate_audit.get_report(
                    connection, catalog_id, revision)
            if report is None:
                handler.send_json(
                    {"error": "Brak zapisanego audytu dla tej rewizji."}, 404)
            else:
                handler.send_json(report)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            handler.send_json({"error": str(exc)}, 422)
        return True

    try:
        items = hf_candidate_audit.load_catalog()
        with league.connect() as connection:
            reports = hf_candidate_audit.list_reports(connection)
            reviews = hf_candidate_reviews.list_reviews(connection)
        report_map = {(item["catalog_id"], item["revision"]): item
                      for item in reports}
        review_map = {(item["catalog_id"], item["revision"]): item
                      for item in reviews}
        result = []
        for item in items:
            row = {key: item.get(key) for key in CANDIDATE_FIELDS}
            key = (item.get("catalog_id"),
                   item.get("revision") or item.get("audited_revision"))
            row["last_audit"] = report_map.get(key)
            row["last_review"] = review_map.get(key)
            result.append(row)
        handler.send_json(result)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        handler.send_json({"error": str(exc)}, 503)
    return True
