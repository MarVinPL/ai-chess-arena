"""Issue #314: consolidated machine endpoint inventory across Worker PR #296.

All Worker POST routes are handled by worker_backend before panel-session
authentication. This test fails if an upstream Worker endpoint is dropped,
renamed, unexpectedly exposed, or silently added without an explicit review.
"""
from pathlib import Path

import worker_backend
from remote_worker_phone_api import ROUTES as PHONE_ROUTES
from worker_update_hold_api import ROUTES as HOLD_ROUTES

EXPECTED_WORKER_POST_ROUTES = frozenset({
    "/api/remote-workers/authenticate",
    "/api/remote-workers/job-poll",
    "/api/remote-workers/ready",
    "/api/remote-workers/job-result",
    "/api/remote-workers/update-pause-poll",
    "/api/remote-workers/board-view",
    "/api/remote-workers/update-resume-status",
    "/api/remote-workers/update-safety",
    "/api/remote-workers/model-source",
    "/api/remote-workers/update-reserve",
    "/api/remote-workers/update-release",
    "/api/remote-workers/update-hold-status",
})


def test_consolidated_worker_route_inventory_is_exact_and_machine_only():
    assert worker_backend.WORKER_POST_ROUTES == EXPECTED_WORKER_POST_ROUTES
    assert worker_backend.AUTH_ROUTE in EXPECTED_WORKER_POST_ROUTES
    assert len(PHONE_ROUTES) == 5
    assert len(HOLD_ROUTES) == 3
    assert all(path.startswith(worker_backend.WORKER_ROUTE_PREFIX)
               for path in EXPECTED_WORKER_POST_ROUTES)


def test_worker_routes_are_all_owned_before_browser_auth():
    root = Path(__file__).resolve().parents[1]
    server = (root / "server.py").read_text(encoding="utf-8")
    post = server.split("    def do_POST(self):", 1)[1]
    dispatch = "worker_backend.dispatch_worker_post(self, path, DB, worker_live_snapshot)"
    assert post.count(dispatch) == 1
    assert post.index(dispatch) < post.index("web_panel_auth.dispatch_panel_auth_post(")
    assert post.index(dispatch) < post.index("if not self.authenticated_user():")
    assert "if path == '/api/remote-workers/authenticate':" not in post
    assert "if path in ROUTES:" not in post


def test_worker_status_reads_do_not_become_install_or_checkpoint_authority():
    root = Path(__file__).resolve().parents[1]
    paths = (
        "worker_update_safety_api.py",
        "worker_update_resume_status_api.py",
        "worker_model_source_api.py",
    )
    for path in paths:
        source = (root / path).read_text(encoding="utf-8")
        assert "WorkerTokenStore(" in source
        assert "assign_from_arena(" not in source
        assert "reconcile_resume(" not in source
        assert "checkpoint_json" not in source
    holds = (root / "worker_update_hold_api.py").read_text(encoding="utf-8")
    assert "DurableWorkerJobs(database)" in holds
    assert '"install_allowed": False' in holds
    assert "release_update_hold(worker_id, hold_token)" in holds
