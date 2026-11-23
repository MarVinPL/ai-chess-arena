"""Model storage API isolation, lock and confirmation regressions (#314)."""
from pathlib import Path
from unittest.mock import Mock

import pytest
import web_panel_model_storage_api as api


def test_storage_routes_are_exact_and_do_not_accept_worker_paths():
    assert api.MODEL_STORAGE_POST_ROUTES == {
        "/api/models/download", "/api/models/cleanup"
    }
    with pytest.raises(ValueError, match="Unknown"):
        api.handle_model_storage_post("/api/remote-workers/job-poll", {})


def test_download_requires_literal_true_before_service_call(monkeypatch):
    download = Mock()
    monkeypatch.setattr(api.model_store, "download_version", download)
    for confirmation in (None, 0, 1, "true", False):
        with pytest.raises(ValueError, match="potwierdzenie"):
            api.handle_model_storage_post("/api/models/download", {
                "confirm_download": confirmation
            })
    download.assert_not_called()


def test_download_reuses_existing_model_store_and_preserves_arguments(monkeypatch):
    download = Mock(return_value={"downloaded": True})
    monkeypatch.setattr(api.model_store, "download_version", download)
    result = api.handle_model_storage_post("/api/models/download", {
        "confirm_download": True, "version_id": "11", "url": "https://example.org/model",
        "filename": "model.gguf", "sha256": "a" * 64,
        "size_bytes": "123", "license_verified": True,
    })
    assert result == {"downloaded": True}
    download.assert_called_once_with(11, "https://example.org/model", "model.gguf",
                                     "a" * 64, 123, True)


def test_cleanup_requires_confirmation_and_refuses_busy_lifecycle_lock(monkeypatch):
    cleanup = Mock()
    lock = Mock()
    lock.acquire.return_value = False
    monkeypatch.setattr(api.model_store, "cleanup_version", cleanup)
    monkeypatch.setattr(api.model_store, "MODEL_LIFECYCLE_LOCK", lock)
    with pytest.raises(ValueError, match="potwierdzenie"):
        api.handle_model_storage_post("/api/models/cleanup", {
            "confirm_delete": 1, "version_id": 2
        })
    lock.acquire.assert_not_called()
    with pytest.raises(RuntimeError, match="aktywnego turnieju"):
        api.handle_model_storage_post("/api/models/cleanup", {
            "confirm_delete": True, "version_id": 2
        })
    cleanup.assert_not_called()
    lock.release.assert_not_called()


def test_cleanup_releases_original_lock_after_success_and_exception(monkeypatch):
    lock = Mock()
    lock.acquire.return_value = True
    monkeypatch.setattr(api.model_store, "MODEL_LIFECYCLE_LOCK", lock)
    cleanup = Mock(return_value={"removed": True})
    monkeypatch.setattr(api.model_store, "cleanup_version", cleanup)
    assert api.handle_model_storage_post("/api/models/cleanup", {
        "confirm_delete": True, "version_id": "3"
    }) == {"removed": True}
    cleanup.assert_called_once_with(3)
    lock.release.assert_called_once()
    cleanup.side_effect = RuntimeError("failed cleanup")
    with pytest.raises(RuntimeError, match="failed cleanup"):
        api.handle_model_storage_post("/api/models/cleanup", {
            "confirm_delete": True, "version_id": "3"
        })
    assert lock.release.call_count == 2


def test_storage_actions_remain_behind_panel_session_and_origin_checks():
    source = (Path(__file__).resolve().parents[1] / "server.py").read_text()
    post = source.split("    def do_POST(self):", 1)[1]
    dispatch = post.index("web_panel_model_storage_api.handle_model_storage_post(")
    assert post.index("worker_backend.dispatch_worker_post(") < dispatch
    assert post.index("if not self.authenticated_user():") < dispatch
    assert post.index("if not arena_auth.origin_allowed(") < dispatch
    module = (Path(__file__).resolve().parents[1] /
              "web_panel_model_storage_api.py").read_text()
    assert "WorkerTokenStore" not in module
    assert "sqlite3.connect" not in module
