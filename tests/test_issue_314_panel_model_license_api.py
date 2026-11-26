"""Regression tests for model licensing behind the panel boundary (#314)."""
import json
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock

import pytest

import web_panel_model_license_api as api


class Connection:
    def __init__(self, rows):
        self.rows = rows
        self.statements = []

    def execute(self, query, args):
        self.statements.append((query, args))
        if query.startswith("UPDATE"):
            return None
        if not self.rows:
            return Mock(fetchone=lambda: None)
        value = self.rows.pop(0)
        return Mock(fetchone=lambda: value)


def connect_to(connection):
    @contextmanager
    def database():
        yield connection
    return database


def test_admin_license_routes_are_exact_and_worker_routes_refused():
    assert len(api.MODEL_LICENSE_POST_ROUTES) == 3
    assert "/api/remote-workers/ready" not in api.MODEL_LICENSE_POST_ROUTES
    with pytest.raises(ValueError, match="Unknown"):
        api.handle_model_license_post(
            "/api/remote-workers/ready", {}, fetch_evidence=Mock()
        )


def test_registration_uses_authoritative_model_service(monkeypatch):
    register = Mock(return_value={"id": 10})
    monkeypatch.setattr(api.league, "register_model", register)
    assert api.handle_model_license_post(
        "/api/models/register", {"name": "Model A"}, fetch_evidence=Mock()
    ) == {"id": 10}
    register.assert_called_once_with(
        "Model A", None, "", "", None, "PURE_AI", False
    )


def test_verify_license_requires_explicit_boolean_confirmation(monkeypatch):
    connect = Mock()
    monkeypatch.setattr(api.league, "connect", connect)
    for value in (None, 1, "true", False):
        with pytest.raises(ValueError, match="potwierdzenie"):
            api.handle_model_license_post(
                "/api/models/verify-license",
                {"version_id": 3, "confirm_review": value},
                fetch_evidence=Mock(),
            )
    connect.assert_not_called()


def test_license_evidence_rejects_excluded_model_before_fetch(monkeypatch):
    connection = Connection([
        {"id": 3, "source": "https://example.invalid", "metadata_json": "{}",
         "name": "excluded"}
    ])
    monkeypatch.setattr(api.league, "connect", connect_to(connection))
    monkeypatch.setattr(api, "is_excluded_model_identity", lambda **kw: True)
    fetch = Mock()
    with pytest.raises(ValueError, match="wyłączony"):
        api.handle_model_license_post(
            "/api/models/license-evidence", {"version_id": 3},
            fetch_evidence=fetch,
        )
    fetch.assert_not_called()
    assert not any(sql.startswith("UPDATE") for sql, _ in connection.statements)


def test_evidence_updates_existing_model_metadata_on_same_database(monkeypatch):
    connection = Connection([
        {"id": 3, "source": "https://example.invalid", "metadata_json": "{}",
         "name": "allowed"},
        {"metadata_json": '{"original":1}'},
    ])
    monkeypatch.setattr(api.league, "connect", connect_to(connection))
    monkeypatch.setattr(api, "is_excluded_model_identity", lambda **kw: False)
    fetch = Mock(return_value={"license": "MIT"})
    result = api.handle_model_license_post(
        "/api/models/license-evidence", {"version_id": 3},
        fetch_evidence=fetch,
    )
    assert result == {
        "version_id": 3, "evidence": {"license": "MIT"},
        "license_verified": False
    }
    fetch.assert_called_once_with("https://example.invalid")
    updates = [(sql, args) for sql, args in connection.statements
               if sql.startswith("UPDATE")]
    assert len(updates) == 1 and updates[0][1][1] == 3
    assert json.loads(updates[0][1][0]) == {
        "original": 1, "license_evidence": {"license": "MIT"}
    }


def test_verify_license_requires_evidence_when_source_lacks_license(monkeypatch):
    connection = Connection([
        {"id": 3, "source": "https://example.invalid/model",
         "license": "", "metadata_json": "{}"},
        {"name": "candidate"},
    ])
    monkeypatch.setattr(api.league, "connect", connect_to(connection))
    monkeypatch.setattr(api, "is_excluded_model_identity", lambda **kw: False)
    with pytest.raises(ValueError, match="dowody"):
        api.handle_model_license_post(
            "/api/models/verify-license",
            {"version_id": 3, "confirm_review": True, "license": "MIT"},
            fetch_evidence=Mock(),
        )
    assert not any(sql.startswith("UPDATE") for sql, _ in connection.statements)


def test_verification_writes_authoritative_license_state(monkeypatch):
    connection = Connection([
        {"id": 3, "source": "https://example.invalid/model",
         "license": "", "metadata_json": '{"license_evidence":{"license":"MIT"}}'},
        {"name": "candidate"},
    ])
    monkeypatch.setattr(api.league, "connect", connect_to(connection))
    monkeypatch.setattr(api, "is_excluded_model_identity", lambda **kw: False)
    result = api.handle_model_license_post(
        "/api/models/verify-license",
        {"version_id": 3, "confirm_review": True, "license": "MIT"},
        fetch_evidence=Mock(),
    )
    assert result == {"version_id": 3, "license_verified": True}
    assert any(sql.startswith("UPDATE") and args == ("MIT", 3)
               for sql, args in connection.statements)


def test_license_panel_dispatch_stays_after_browser_auth_and_origin():
    source = (Path(__file__).resolve().parents[1] / "server.py").read_text()
    post = source.split("    def do_POST(self):", 1)[1]
    dispatch = post.index("web_panel_model_license_api.handle_model_license_post(")
    assert post.index("worker_backend.dispatch_worker_post(") < dispatch
    assert post.index("if not self.authenticated_user():") < dispatch
    assert post.index("if not arena_auth.origin_allowed(") < dispatch
    module = (Path(__file__).resolve().parents[1] / "web_panel_model_license_api.py").read_text()
    assert "WorkerTokenStore" not in module
    assert "sqlite3.connect" not in module
