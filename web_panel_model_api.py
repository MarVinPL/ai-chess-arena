"""Authenticated browser-panel model endpoints.

These handlers are entered only AFTER server.Handler has checked the panel
session (and Origin on POST). They must never accept Worker machine tokens
as a substitute for that boundary. All database operations reuse league's
authoritative connection, not a separate model registry.
"""
import league
import model_permissions
import model_review_submissions

MODEL_GET_ROUTES = frozenset({
    "/api/model-permissions",
    "/api/model-review-submissions",
})
PERMISSION_POST_ROUTES = frozenset({
    "/api/model-permissions/request",
    "/api/model-permissions/decision",
})


def dispatch_panel_model_get(handler, path):
    """Handle authenticated model-admin reads, preserving existing status codes."""
    if path == "/api/model-permissions":
        try:
            with league.connect() as connection:
                handler.send_json(model_permissions.list_permissions(connection))
        except Exception as exc:
            handler.send_json({"error": str(exc)}, 500)
        return True
    if path == "/api/model-review-submissions":
        try:
            with league.connect() as connection:
                handler.send_json(model_review_submissions.list_submissions(connection))
        except Exception as exc:
            handler.send_json({"error": str(exc)}, 500)
        return True
    return False


def handle_permission_post(path, payload, actor):
    """Implement authenticated admin write; caller owns the HTTP status policy.

    This does NOT authenticate a caller. Only the panel's session+Origin-checked
    composition root can call it; neither API tokens nor Workers can route here.
    """
    if path == "/api/model-permissions/request":
        with league.connect() as connection:
            return model_permissions.request_permission(
                connection, model_id=payload.get("model_id"),
                repo_id=payload.get("repo_id"),
                revision=payload.get("revision"),
                artifact_sha256=payload.get("artifact_sha256"),
                scope=payload.get("scope"),
                confirm=payload.get("confirm") is True,
                reason=payload.get("reason", ""),
                snapshot=payload.get("snapshot"),
                actor=actor,
            )
    if path == "/api/model-permissions/decision":
        request_id = payload.get("request_id")
        if isinstance(request_id, bool) or not isinstance(request_id, int):
            raise ValueError("Nieprawidłowy identyfikator żądania.")
        with league.connect() as connection:
            return model_permissions.decide_permission(
                connection, request_id=request_id,
                action=payload.get("action"),
                confirm=payload.get("confirm") is True,
                reason=payload.get("reason", ""),
                actor=actor,
            )
    raise ValueError("Unknown model permission route")
