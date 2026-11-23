"""Panel-only GGUF storage administration (Issue #314).

Called exclusively after the HTTP composition root validates a browser
session and Origin. All downloads and deletions reuse model_store's
authoritative service and lifecycle lock. Worker tokens are not accepted.
"""
import model_store

MODEL_STORAGE_POST_ROUTES = frozenset({
    "/api/models/download",
    "/api/models/cleanup",
})


def handle_model_storage_post(path, payload):
    """Preserve existing confirmation, argument, lock and result semantics."""
    if path == "/api/models/download":
        if payload.get("confirm_download") is not True:
            raise ValueError("Wymagane jawne potwierdzenie pobrania")
        return model_store.download_version(
            int(payload.get("version_id", 0)),
            payload.get("url", ""),
            payload.get("filename", ""),
            payload.get("sha256", ""),
            int(payload.get("size_bytes", 0)),
            bool(payload.get("license_verified", False)),
        )
    if path == "/api/models/cleanup":
        if payload.get("confirm_delete") is not True:
            raise ValueError("Wymagane jawne potwierdzenie usunięcia kopii areny")
        if not model_store.MODEL_LIFECYCLE_LOCK.acquire(blocking=False):
            raise RuntimeError(
                "Nie można usuwać modeli podczas aktywnego turnieju lub pobierania; spróbuj później"
            )
        try:
            return model_store.cleanup_version(int(payload.get("version_id", 0)))
        finally:
            model_store.MODEL_LIFECYCLE_LOCK.release()
    raise ValueError("Unknown model storage admin route")
