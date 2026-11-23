"""Issue #314: enforce distinct panel and machine-token HTTP namespaces.

The draft Worker branches #197/#296 may introduce more worker endpoints.
They must be integrated into worker_backend (and individually authenticated)
before merge. The panel dispatch must never silently claim those paths.
"""
from pathlib import Path

import web_panel_download_api
import web_panel_game_control_api
import web_panel_hf_review_api
import web_panel_model_api
import web_panel_model_license_api
import web_panel_model_review_api
import web_panel_model_storage_api
import web_panel_tournament_api
import worker_backend


PANEL_POST_ROUTES = (
    web_panel_download_api.ADMIN_DOWNLOAD_POST_ROUTES
    | web_panel_game_control_api.PANEL_GAME_CONTROL_POST_ROUTES
    | web_panel_hf_review_api.HF_REVIEW_POST_ROUTES
    | web_panel_model_api.PERMISSION_POST_ROUTES
    | web_panel_model_license_api.MODEL_LICENSE_POST_ROUTES
    | web_panel_model_review_api.MODEL_REVIEW_POST_ROUTES
    | web_panel_model_storage_api.MODEL_STORAGE_POST_ROUTES
    | web_panel_tournament_api.TOURNAMENT_POST_ROUTES
)


def test_worker_and_panel_namespaces_do_not_overlap():
    assert PANEL_POST_ROUTES
    assert worker_backend.WORKER_POST_ROUTES
    assert not (PANEL_POST_ROUTES & worker_backend.WORKER_POST_ROUTES)
    assert all(not route.startswith("/api/remote-workers/")
               for route in PANEL_POST_ROUTES)
    assert all(route.startswith("/api/remote-workers/")
               for route in worker_backend.WORKER_POST_ROUTES)


def test_only_worker_backend_owns_worker_http_dispatch_in_composition_root():
    source = (Path(__file__).resolve().parents[1] / "server.py").read_text(
        encoding="utf-8")
    post = source.split("    def do_POST(self):", 1)[1]
    assert post.count("worker_backend.dispatch_worker_post(") == 1
    assert "if path == '/api/remote-workers/" not in post
    assert "if path in ROUTES:" not in post
    assert post.index("worker_backend.dispatch_worker_post(") < post.index(
        "if not self.authenticated_user():"
    )


def test_unintegrated_update_routes_must_not_be_implicitly_authorized():
    """Draft #296 machine operations require explicit integration, not cookies."""
    integrated = "/api/remote-workers/update-resume-status"
    assert integrated not in PANEL_POST_ROUTES
    assert integrated in worker_backend.WORKER_POST_ROUTES
    for route in (
        "/api/remote-workers/update-reserve",
        "/api/remote-workers/update-release",
        "/api/remote-workers/update-hold-status",
        "/api/remote-workers/model-source",
    ):
        assert route not in PANEL_POST_ROUTES
        # Remaining draft-only operations must be migrated explicitly.
        assert route not in worker_backend.WORKER_POST_ROUTES
