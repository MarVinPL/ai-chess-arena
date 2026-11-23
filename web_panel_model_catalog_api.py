"""Authenticated browser-panel model catalog read API (Issue #314).

The HTTP composition root validates browser sessions before delegation.
This module reuses league's authoritative SQLite connection and inference
normalization: it does not create a second model registry or Worker endpoint.
"""
import json

import league

MODEL_CATALOG_GET_ROUTES = frozenset({"/api/models"})


def dispatch_panel_model_catalog_get(handler, path):
    """Preserve the existing model catalog payload and its fallback fields."""
    if path not in MODEL_CATALOG_GET_ROUTES:
        return False
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
    return True
