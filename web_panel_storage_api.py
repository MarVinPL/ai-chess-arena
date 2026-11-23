"""Authenticated panel storage overview GET endpoint (Issue #314).

Only the browser-session-checked HTTP router invokes this adapter. The
existing league model storage domain service and managed-model directory
remain authoritative; no Worker-token path or duplicate model storage.
"""
import shutil

import league
import model_store

STORAGE_GET_ROUTES = frozenset({"/api/storage"})


def dispatch_panel_storage_get(handler, path):
    """Preserve the existing storage payload, directory policy and status."""
    if path not in STORAGE_GET_ROUTES:
        return False
    status = league.storage_status()
    managed = model_store.MANAGED_DIR
    managed.mkdir(mode=0o700, parents=True, exist_ok=True)
    status["managed_directory"] = str(managed)
    status["managed_models"] = [
        {"filename": model.name, "size_bytes": model.stat().st_size}
        for model in sorted(managed.glob("*.gguf")) if model.is_file()
    ]
    status["managed_free_bytes"] = shutil.disk_usage(managed).free
    handler.send_json(status)
    return True
