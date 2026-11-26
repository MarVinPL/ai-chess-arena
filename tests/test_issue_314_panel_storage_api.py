"""Issue #314: authenticated storage overview preserves the old payload."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import web_panel_storage_api as api


class Reply:
    def __init__(self):
        self.responses = []

    def send_json(self, data, status=200):
        self.responses.append((status, data))


def test_storage_route_does_not_intercept_worker_or_unknown_paths():
    reply = Reply()
    assert not api.dispatch_panel_storage_get(reply, "/api/remote-workers/storage")
    assert not api.dispatch_panel_storage_get(reply, "/api/storage/../other")
    assert reply.responses == []


def test_storage_overview_uses_existing_directory_and_domain_service(tmp_path, monkeypatch):
    managed = tmp_path / "managed-models"
    monkeypatch.setattr(api.model_store, "MANAGED_DIR", managed)
    status = Mock(return_value={"database": "ready"})
    monkeypatch.setattr(api.league, "storage_status", status)
    monkeypatch.setattr(api.shutil, "disk_usage", lambda directory:
                        SimpleNamespace(free=1234567))
    reply = Reply()
    assert api.dispatch_panel_storage_get(reply, "/api/storage")
    status.assert_called_once_with()
    assert managed.is_dir()
    assert managed.stat().st_mode & 0o777 == 0o700
    assert reply.responses == [(200, {
        "database": "ready",
        "managed_directory": str(managed),
        "managed_models": [],
        "managed_free_bytes": 1234567,
    })]


def test_only_managed_gguf_files_are_listed_in_sorted_order(tmp_path, monkeypatch):
    managed = tmp_path / "managed"
    managed.mkdir()
    (managed / "z.gguf").write_bytes(b"123")
    (managed / "a.gguf").write_bytes(b"12")
    (managed / "other.txt").write_bytes(b"ignored")
    (managed / "nested.gguf").mkdir()
    monkeypatch.setattr(api.model_store, "MANAGED_DIR", managed)
    monkeypatch.setattr(api.league, "storage_status", lambda: {})
    monkeypatch.setattr(api.shutil, "disk_usage", lambda directory:
                        SimpleNamespace(free=999))
    reply = Reply()
    assert api.dispatch_panel_storage_get(reply, "/api/storage")
    assert reply.responses[0][1]["managed_models"] == [
        {"filename": "a.gguf", "size_bytes": 2},
        {"filename": "z.gguf", "size_bytes": 3},
    ]


def test_storage_domain_failures_are_not_silently_swallowed(monkeypatch):
    def fail():
        raise OSError("storage not available")
    monkeypatch.setattr(api.league, "storage_status", fail)
    reply = Reply()
    try:
        api.dispatch_panel_storage_get(reply, "/api/storage")
    except OSError as exc:
        assert str(exc) == "storage not available"
    else:
        raise AssertionError("Legacy handler lets domain failures propagate")
    assert reply.responses == []


def test_storage_is_only_reachable_after_browser_session_gate():
    root = Path(__file__).resolve().parents[1]
    source = (root / "server.py").read_text("utf-8")
    get = source.split("    def do_GET(self):", 1)[1].split("    def do_POST(self):", 1)[0]
    assert get.index("if not user:") < get.index(
        "web_panel_storage_api.dispatch_panel_storage_get(")
    adapter = (root / "web_panel_storage_api.py").read_text("utf-8")
    assert "WorkerTokenStore" not in adapter
    assert "sqlite3.connect" not in adapter
