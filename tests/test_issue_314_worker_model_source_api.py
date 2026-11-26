"""Issue #314: model-source never grants cross-model access or installation."""
import io
import json
from unittest.mock import Mock

import pytest
import worker_backend
import worker_model_source_api as api


class Reply:
    def __init__(self, raw):
        self.headers = {"Content-Length": str(len(raw)), "Cookie": "arena_session=admin"}
        self.rfile = io.BytesIO(raw)
        self.responses = []

    def send_json(self, value, status=200, *_):
        self.responses.append((status, value))


def body(**changes):
    data = {"worker_id": "android_12345678", "token": "model-token", "nonce": "a"*32}
    data.update(changes)
    return json.dumps(data).encode("utf-8")


@pytest.fixture
def deps(monkeypatch):
    tokens = Mock()
    tokens.return_value.authenticate.return_value = True
    delivery = Mock()
    assignment = {"state": "assigned", "job": {"model_sha256": "a"*64}}
    delivery.return_value.poll_assigned_job.return_value = assignment
    approved = Mock(return_value={
        "sha256": "a"*64, "source_url": "https://huggingface.co/owner/model/resolve/"+"b"*40+"/model.gguf"
    })
    monkeypatch.setattr(api, "WorkerTokenStore", tokens)
    monkeypatch.setattr(api, "WorkerInferenceDelivery", delivery)
    monkeypatch.setattr(api, "resolve_approved_worker_gguf", approved)
    return tokens, delivery, approved, assignment


def test_model_source_is_registered_before_panel_session(monkeypatch, tmp_path):
    handler = Reply(body())
    action = Mock()
    monkeypatch.setattr(worker_backend, "handle_model_source", action)
    assert api.ROUTE in worker_backend.WORKER_POST_ROUTES
    assert worker_backend.dispatch_worker_post(handler, api.ROUTE, tmp_path/"db", {})
    action.assert_called_once_with(handler, tmp_path/"db")


def test_admin_cookie_cannot_bypass_machine_token(deps, tmp_path):
    tokens, delivery, approved, _ = deps
    tokens.return_value.authenticate.return_value = False
    handler = Reply(body())
    api.handle_model_source(handler, tmp_path/"db")
    assert handler.responses == [(401, {"error": "Brak autoryzacji"})]
    delivery.assert_not_called()
    approved.assert_not_called()


def test_only_current_assigned_sha_is_resolved_and_lease_rechecked(deps, tmp_path):
    tokens, delivery, approved, assignment = deps
    handler = Reply(body())
    api.handle_model_source(handler, tmp_path/"db")
    code, response = handler.responses[0]
    assert code == 200
    assert response["state"] == "approved_metadata_only"
    assert response["model"]["sha256"] == "a"*64
    assert "install_allowed" not in response
    tokens.return_value.authenticate.assert_called_once_with("android_12345678", "model-token")
    approved.assert_called_once_with(tmp_path/"db", "a"*64)
    assert delivery.return_value.poll_assigned_job.call_count == 2


@pytest.mark.parametrize("changed", [
    {"state": "none"}, {"state": "blocked"}, {"state": "awaiting_arena"},
])
def test_no_current_assignment_never_queries_model_registry(deps, tmp_path, changed):
    _, delivery, approved, _ = deps
    delivery.return_value.poll_assigned_job.return_value = changed
    handler = Reply(body())
    api.handle_model_source(handler, tmp_path/"db")
    assert handler.responses == [(409, {"error": "Brak aktywnego, ważnego przydziału"})]
    approved.assert_not_called()


def test_changed_or_revoked_lease_denies_metadata(deps, tmp_path):
    _, delivery, approved, assignment = deps
    delivery.return_value.poll_assigned_job.side_effect = [
        assignment, {"state": "assigned", "job": {"model_sha256": "c"*64}},
    ]
    handler = Reply(body())
    api.handle_model_source(handler, tmp_path/"db")
    assert handler.responses == [(409, {"error": "Przydział uległ zmianie"})]
    approved.assert_called_once_with(tmp_path/"db", "a"*64)


def test_non_admitted_model_returns_conflict_without_metadata(deps, tmp_path):
    _, _, approved, _ = deps
    approved.side_effect = api.WorkerModelRegistryError("private db path")
    handler = Reply(body())
    api.handle_model_source(handler, tmp_path/"db")
    assert handler.responses == [(409, {"error": "Model nie jest dopuszczony"})]


def test_caller_cannot_select_arbitrary_sha_or_url(deps, tmp_path):
    tokens, delivery, approved, _ = deps
    for changes in ({"model_sha256": "d"*64}, {"source_url": "https://evil.invalid/model.gguf"},
                    {"revision": "c"*40}, {"install_allowed": True}):
        handler = Reply(body(**changes))
        api.handle_model_source(handler, tmp_path/"db")
        assert handler.responses[0][0] == 400
    tokens.assert_not_called()
    delivery.assert_not_called()
    approved.assert_not_called()


def test_rejects_malformed_nonce_json_and_oversized_body(deps, tmp_path):
    tokens, delivery, approved, _ = deps
    for raw in (body(nonce="A"*32), body(nonce="invalid"), b"{", b"[]", b"x"*2049):
        handler = Reply(raw)
        api.handle_model_source(handler, tmp_path/"db")
        assert handler.responses[0][0] == 400
        if len(raw)>2048:
            assert handler.rfile.tell() == 0
    tokens.assert_not_called()
    delivery.assert_not_called()
    approved.assert_not_called()
