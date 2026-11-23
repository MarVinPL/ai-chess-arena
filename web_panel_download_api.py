"""Panel-only administrative Hugging Face download operations (Issue #314).

Invoked exclusively AFTER the main HTTP composition root checks browser
session and Origin. A Worker machine token cannot authorize these routes.
Reuse the existing downloader and shared Arena database/registry.
"""
import hf_candidate_downloads

ADMIN_DOWNLOAD_POST_ROUTES = frozenset({
    "/api/hf-candidates/download-start",
    "/api/hf-candidates/download-cancel",
    "/api/hf-candidates/download-resume",
    "/api/hf-candidates/download-plan/refresh",
})


def handle_download_admin_post(path, payload):
    """Preserve existing confirmation and error semantics for admin actions."""
    if path == "/api/hf-candidates/download-cancel":
        return hf_candidate_downloads.cancel(payload.get("job_id"))
    if path == "/api/hf-candidates/download-resume":
        if payload.get("confirm_resume") is not True:
            raise ValueError("Wymagane jawne zatwierdzenie wznowienia pobierania.")
        return hf_candidate_downloads.resume(payload.get("job_id"))
    if path == "/api/hf-candidates/download-plan/refresh":
        return hf_candidate_downloads.refresh_plan()
    if path == "/api/hf-candidates/download-start":
        if payload.get("confirm_download") is not True:
            raise ValueError("Wymagane jawne zatwierdzenie pobrania partii.")
        return hf_candidate_downloads.start(payload.get("candidates"), source="manual")
    raise ValueError("Unknown administrative download route")
