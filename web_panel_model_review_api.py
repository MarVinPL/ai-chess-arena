"""Panel-only model submissions and admission decisions (Issue #314).

Invoked AFTER the browser session and Origin checks in server.Handler.
The existing league connection and allowlist remain authoritative; Worker
tokens and HTTP route dispatch are deliberately absent from this module.
"""
import league
import model_admission
import model_allowlist
import model_review_submissions

MODEL_REVIEW_POST_ROUTES = frozenset({
    "/api/model-review-submissions/action",
    "/api/model-review-submissions",
    "/api/model-admission/admit",
    "/api/model-admission/revoke",
})


def _eligible_review_ids(connection):
    """Preserve legacy candidate filtering rather than creating another list."""
    return {
        item["model_id"]
        for item in model_allowlist.load_allowlist(connection)["models"]
        if item.get("status") in {"eligible", "restricted"} and item["model_id"] != 6
    }


def handle_model_review_admin_post(path, payload):
    """Execute a recognized, already-authorized panel operation."""
    if path == "/api/model-review-submissions/action":
        with league.connect() as connection:
            allowed_ids = _eligible_review_ids(connection)
            return model_review_submissions.apply_action(
                connection,
                submission_id=payload.get("submission_id"),
                action=payload.get("action"),
                actor="arena",
                reason=payload.get("reason", ""),
                payload=payload.get("proposal"),
                allowed_model_ids=allowed_ids,
            )
    if path == "/api/model-review-submissions":
        with league.connect() as connection:
            allowed_ids = _eligible_review_ids(connection)
            return model_review_submissions.submit(
                connection, payload, allowed_model_ids=allowed_ids)
    if path in ("/api/model-admission/admit", "/api/model-admission/revoke"):
        with league.connect() as connection:
            return model_admission.apply_decision(
                connection,
                model_id=int(payload.get("model_id", 0)),
                action="admit" if path.endswith("/admit") else "revoke",
                confirm=payload.get("confirm") is True,
                reason=payload.get("reason", ""),
            )
    raise ValueError("Unknown model review/admin route")
