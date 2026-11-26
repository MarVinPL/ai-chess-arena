"""Issue #314: PR #296 resume status is machine-only and read-only."""
import io
import json
from unittest.mock import Mock

import worker_backend
import worker_update_resume_status_api as api


class Reply:
    def __init__(self, payload, *, cookie=True):
        self.headers = {"Content-Length": str(len(payload))}
        if cookie:
            self.headers["Cookie"] = "arena_session=administrator"
        self.rfile = io.BytesIO(payload)
        self.responses = []

    def send_json(self, body, status=200, *_):
        self.responses.append((status, body))


def payload(**changes):
    body = {"worker_id": "android_12345678", "token": "example-token",
            "nonce": "f" * 32}
    body.update(changes)
    return json.dumps(body).encode("utf-8")


def test_machine_route_dispatched_before_panel_session_and_registered(monkeypatch, tmp_path):
    marker = Mock()
    monkeypatch.setattr(worker_backend, "handle_update_resume_status", marker)
    h = Reply(payload())
    assert api.ROUTE in worker_backend.WORKER_POST_ROUTES
    assert worker_backend.dispatch_worker_post(h, api.ROUTE, tmp_path / "db", {})
    marker.assert_called_once_with(h, tmp_path / "db")


def test_invalid_machine_token_cannot_use_admin_cookie(monkeypatch, tmp_path):
    auth = Mock()
    auth.return_value.authenticate.return_value = False
    monkeypatch.setattr(api, "WorkerTokenStore", auth)
    status = Mock()
    monkeypatch.setattr(api, "WorkerUpdatePause", status)
    h = Reply(payload())
    assert worker_backend.dispatch_worker_post(h, api.ROUTE, tmp_path / "db", {})
    assert h.responses == [(401, {"error": "Brak autoryzacji"})]
    status.assert_not_called()


def test_valid_token_returns_original_read_only_contract(monkeypatch, tmp_path):
    tokens = Mock()
    tokens.return_value.authenticate.return_value = True
    pause = Mock()
    pause.return_value.resume_status.return_value = {"state": "blocked", "resume_available": False}
    monkeypatch.setattr(api, "WorkerTokenStore", tokens)
    monkeypatch.setattr(api, "WorkerUpdatePause", pause)
    h = Reply(payload())
    assert worker_backend.dispatch_worker_post(h, api.ROUTE, tmp_path / "db", {})
    assert h.responses == [(200, {
        "authenticated": True, "source": "coordinator",
        "worker_id": "android_12345678", "nonce": "f" * 32,
        "resume_available": False, "state": "blocked",
    })]
    tokens.return_value.authenticate.assert_called_once_with("android_12345678", "example-token")
    pause.return_value.resume_status.assert_called_once_with("android_12345678")


def test_rejects_malformed_nonce_or_body_before_token_lookup(monkeypatch, tmp_path):
    auth = Mock()
    monkeypatch.setattr(api, "WorkerTokenStore", auth)
    for raw in (payload(nonce="F"*32), payload(nonce="wrong"), b"{",
                json.dumps(["not", "an", "object"]).encode()):
        h = Reply(raw)
        api.handle_update_resume_status(h, tmp_path / "db")
        assert h.responses[0][0] == 400
    auth.assert_not_called()


def test_excessive_body_is_rejected_without_reading(monkeypatch, tmp_path):
    auth = Mock()
    monkeypatch.setattr(api, "WorkerTokenStore", auth)
    h = Reply(b"x" * 2049)
    api.handle_update_resume_status(h, tmp_path / "db")
    assert h.responses == [(400, {"error": "Nieprawidłowe żądanie"})]
    assert h.rfile.tell() == 0
    auth.assert_not_called()


def test_domain_error_fails_closed_and_does_not_claim_install_permission(monkeypatch, tmp_path):
    tokens = Mock()
    tokens.return_value.authenticate.return_value = True
    status = Mock()
    status.return_value.resume_status.side_effect = OSError("secret path")
    monkeypatch.setattr(api, "WorkerTokenStore", tokens)
    monkeypatch.setattr(api, "WorkerUpdatePause", status)
    h = Reply(payload())
    api.handle_update_resume_status(h, tmp_path / "db")
    assert h.responses == [(500, {"error": "Nie można potwierdzić wznowienia"})]
    assert b"secret" not in json.dumps(h.responses).encode()
