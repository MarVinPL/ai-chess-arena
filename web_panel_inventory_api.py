"""Authenticated panel model inventory and Elo ranking reads (Issue #314).

Only the HTTP composition root authorizes the browser session. Reuses the
existing league database and domain services; this module has no Worker
authentication and does not create an independent registry.
"""
import json

import league
import model_admission
import model_allowlist

MODEL_INVENTORY_GET_ROUTES = frozenset({
    "/api/models",
    "/api/model-admission",
    "/api/league",
    "/api/league/engine",
})


def dispatch_panel_inventory_get(handler, path):
    """Handle exact browser-panel read routes, preserving payload/status codes."""
    if path not in MODEL_INVENTORY_GET_ROUTES:
        return False
    if path == "/api/models":
        with league.connect() as connection:
            rows = connection.execute(
                """SELECT m.id,m.name,m.format,m.active,v.id AS version_id,v.filename,v.sha256,v.size_bytes,v.status,v.license,v.license_verified,v.source,v.managed_copy,v.draft_filename,v.draft_sha256,v.draft_size_bytes,v.draft_status,v.draft_managed_copy,v.metadata_json
                FROM league_models m LEFT JOIN model_versions v ON v.id=(SELECT MAX(id) FROM model_versions WHERE model_id=m.id)
                ORDER BY m.name"""
            ).fetchall()
        models = []
        for row in rows:
            item = dict(row)
            try:
                metadata = json.loads(item.pop("metadata_json") or "{}")
                item["license_evidence"] = metadata.get("license_evidence")
                item["inference"] = league.normalize_inference_config(metadata)
                item["token_budget"] = league.token_budget_info(
                    item.get("sha256"), item.get("draft_sha256"), metadata
                )
            except (TypeError, json.JSONDecodeError, ValueError):
                item["license_evidence"] = None
                item["inference"] = {"thinking": False}
                item.pop("metadata_json", None)
            models.append(item)
        handler.send_json(models)
    elif path == "/api/model-admission":
        try:
            with league.connect() as connection:
                handler.send_json(model_admission.list_decisions(connection))
        except (model_allowlist.AllowlistError, OSError, ValueError) as exc:
            handler.send_json({"error": str(exc)}, 503)
    elif path == "/api/league":
        handler.send_json(league.elo_ratings("PURE_AI"))
    else:
        handler.send_json(league.elo_ratings("AI_ENGINE"))
    return True
