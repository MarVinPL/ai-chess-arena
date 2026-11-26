"""Issue #314: update-safety is machine-only and never install permission."""
import io
import json
from unittest.mock import Mock

import worker_backend
import worker_update_safety_api as api


class Reply:
    def __init__(self, body, *, cookie=True):
        self.headers = {"Content-Length": str(len(body)), "Content-Type": "application/json"}
        if cookie:
            self.headers["Cookie"] = "arena_session=admin"
        self.rfile = io.BytesIO(body)
        self.responses = []

    def send_json(self, value, status=200, *_):
        self.responses.append((status, value))


def request(**changes):
    data = {"worker_id": "android_12345678", "token": "token-example",
            "nonce": "a" * 32}
    data.update(changes)
    return json.dumps(data).encode("utf-8")


def test_route_registered_and_called_before_browser_session(monkeypatch, tmp_path):
    action = Mock()
    monkeypatch.setattr(worker_backend, "handle_update_safety", action)
    h = Reply(request())
    assert api.ROUTE in worker_backend.WORKER_POST_ROUTES
    assert worker_backend.dispatch_worker_post(h, api.ROUTE, tmp_path / "db", {})
    action.assert_called_once_with(h, tmp_path / "db")


def test_admin_cookie_cannot_replace_machine_token(monkeypatch, tmp_path):
    tokens = Mock()
    tokens.return_value.authenticate.return_value = False
    snapshot = Mock()
    monkeypatch.setattr(api, "WorkerTokenStore", tokens)
    monkeypatch.setattr(api, "DurableWorkerJobs", snapshot)
    h = Reply(request())
    api.handle_update_safety(h, tmp_path / "db")
    assert h.responses == [(401, {"error": "Brak autoryzacji"})]
    snapshot.assert_not_called()


def test_valid_token_returns_read_only_coordinator_evidence(monkeypatch, tmp_path):
    tokens = Mock()
    tokens.return_value.authenticate.return_value = True
    jobs = Mock()
    jobs.return_value.update_safety_snapshot.return_value = ("idle_confirmed", 0)
    monkeypatch.setattr(api, "WorkerTokenStore", tokens)
    monkeypatch.setattr(api, "DurableWorkerJobs", jobs)
    monkeypatch.setattr(api.time, "time", lambda: 123.45)
    h = Reply(request())
    api.handle_update_safety(h, tmp_path / "db")
    code, body = h.responses[0]
    assert code == 200
    assert body["source"] == "coordinator"
    assert body["state"] == "idle_confirmed"
    assert body["active_tasks"] == 0
    assert body["checked_at_unix_ms"] == 123450
    assert body["install_allowed"] is False
    assert body["worker_id"] == "android_12345678"
    assert body["nonce"] == "a" * 32
    tokens.return_value.authenticate.assert_called_once_with("android_12345678", "token-example")
    jobs.return_value.update_safety_snapshot.assert_called_once_with("android_12345678")


def test_unknown_evidence_never_implies_idle_or_install(monkeypatch, tmp_path):
    tokens = Mock()
    tokens.return_value.authenticate.return_value = True
    jobs = Mock()
    jobs.return_value.update_safety_snapshot.return_value = ("unknown", None)
    monkeypatch.setattr(api, "WorkerTokenStore", tokens)
    monkeypatch.setattr(api, "DurableWorkerJobs", jobs)
    h = Reply(request())
    api.handle_update_safety(h, tmp_path / "db")
    assert h.responses[0][1]["state"] == "unknown"
    assert h.responses[0][1]["active_tasks"] is None
    assert h.responses[0][1]["install_allowed"] is False


def test_nonce_json_and_body_limits_fail_closed(monkeypatch, tmp_path):
    tokens = Mock()
    monkeypatch.setattr(api, "WorkerTokenStore", tokens)
    for data in (request(nonce="F" * 32), request(nonce="invalid"), b"{",
                 json.dumps(["not", "an", "object"]).encode("utf-8"),
                 b"x" * 2049):
        h = Reply(data)
        api.handle_update_safety(h, tmp_path / "db")
        assert h.responses[0][0] == 400
        if len(data) > 2048:
            assert h.rfile.tell() == 0
    tokens.assert_not_called()


def test_durable_registry_failure_hides_private_error(monkeypatch, tmp_path):
    tokens = Mock()
    tokens.return_value.authenticate.return_value = True
    registry = Mock()
    registry.return_value.update_safety_snapshot.side_effect = OSError("secret DB file")
    monkeypatch.setattr(api, "WorkerTokenStore", tokens)
    monkeypatch.setattr(api, "DurableWorkerJobs", registry)
    h = Reply(request())
    api.handle_update_safety(h, tmp_path / "db")
    assert h.responses == [(500, {"error": "Nie można potwierdzić stanu zadań"})]
