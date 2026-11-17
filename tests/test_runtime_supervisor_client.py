import json
import threading

import pytest

from runtime_supervisor_client import (
    RuntimeSupervisorClientError,
    RuntimeSupervisorClientRunner,
)


JOB_ID = 42
UNIT = f"ai-chess-arena-runtime-{JOB_ID}.service"
SHA = "a" * 64
SOCKET = "/run/arena-runtime/supervisor.sock"
BRIDGE = f"/run/arena-runtime/job-{JOB_ID}/control.sock"


class FakeSupervisor:
    def __init__(self, *, fail_start=False, fail_stop=False):
        self.actions = []
        self.lease_state = "absent"
        self.fail_start = fail_start
        self.fail_stop = fail_stop

    def __call__(self, socket_path, action, job_id, *, timeout_seconds):
        self.actions.append((socket_path, action, job_id, timeout_seconds))
        if action == "start":
            if self.fail_start:
                self.lease_state = "active"
                raise TimeoutError("response lost after durable start")
            self.lease_state = "active"
            return {
                "job_id": job_id, "unit_name": UNIT,
                "lease_state": "active", "unit_state": "start_requested",
            }
        if action == "status":
            return {
                "job_id": job_id, "lease_exists": self.lease_state != "absent",
                "unit_name": UNIT if self.lease_state != "absent" else None,
                "lease_state": self.lease_state,
            }
        if action == "stop":
            if self.fail_stop:
                raise TimeoutError("stop response lost")
            self.lease_state = "stopped"
            return {"job_id": job_id, "unit_name": UNIT, "lease_state": "stopped"}
        raise AssertionError(action)


class FakeBridge:
    def __init__(self, *, health_status=200, inference_status=200, content="READY"):
        self.calls = []
        self.health_status = health_status
        self.inference_status = inference_status
        self.content = content

    def __call__(self, socket_path, method, path, body=b"", *, timeout_seconds):
        self.calls.append((socket_path, method, path, body, timeout_seconds))
        if method == "GET":
            return self.health_status, b"healthy"
        return self.inference_status, json.dumps({
            "choices": [{"message": {"content": self.content}}]
        }).encode()


def make_runner(supervisor=None, bridge=None):
    supervisor = supervisor or FakeSupervisor()
    bridge = bridge or FakeBridge()
    runner = RuntimeSupervisorClientRunner(
        supervisor_socket=SOCKET, bridge_socket_template="/run/arena-runtime/job-{job_id}/control.sock",
        supervisor_call=supervisor, bridge_call=bridge,
        sleep=lambda _seconds: None,
    )
    return runner, supervisor, bridge


def payload():
    return {"id": JOB_ID, "sha256": SHA, "path": "/must/not/be/used"}


def test_runner_uses_only_job_id_and_supervisor_managed_bridge():
    runner, supervisor, bridge = make_runner()
    result = runner(payload(), 10, lambda: False)
    assert result["ok"] is True
    assert result["response_excerpt"] == "READY"
    assert result["systemd_unit"] == UNIT
    assert result["supervisor_managed"] is True
    assert [call[1] for call in supervisor.actions] == ["start", "stop"]
    assert supervisor.actions[0][0:3] == (SOCKET, "start", JOB_ID)
    assert supervisor.actions[0][3] == 1800.0
    assert [call[2] for call in bridge.calls] == ["/health", "/v1/chat/completions"]
    assert all(call[0] == BRIDGE for call in bridge.calls)


def test_cancellation_during_health_stops_exact_unit():
    runner, supervisor, bridge = make_runner(bridge=FakeBridge(health_status=503))
    calls = 0
    def cancelled():
        nonlocal calls
        calls += 1
        return calls >= 2
    with pytest.raises(InterruptedError):
        runner(payload(), 10, cancelled)
    assert supervisor.lease_state == "stopped"
    assert [call[1] for call in supervisor.actions] == ["start", "stop"]
    assert len(bridge.calls) == 1


def test_start_timeout_with_durable_lease_reconciles_and_stops():
    supervisor = FakeSupervisor(fail_start=True)
    runner, supervisor, _ = make_runner(supervisor=supervisor)
    with pytest.raises(TimeoutError):
        runner(payload(), 10, lambda: False)
    assert supervisor.lease_state == "stopped"
    assert [call[1] for call in supervisor.actions] == ["start", "status", "stop"]


def test_start_error_without_lease_does_not_issue_stop():
    supervisor = FakeSupervisor(fail_start=True)
    def call(socket_path, action, job_id, *, timeout_seconds):
        if action == "start":
            raise PermissionError("denied")
        if action == "status":
            return {"job_id": job_id, "lease_exists": False, "lease_state": "absent"}
        raise AssertionError("stop must not be called without a lease")
    runner = RuntimeSupervisorClientRunner(
        supervisor_socket=SOCKET, supervisor_call=call,
        bridge_call=FakeBridge(), sleep=lambda _seconds: None,
    )
    with pytest.raises(PermissionError):
        runner(payload(), 10, lambda: False)


def test_invalid_start_unit_is_not_trusted_and_is_reconciled():
    supervisor = FakeSupervisor()
    def call(socket_path, action, job_id, *, timeout_seconds):
        result = supervisor(socket_path, action, job_id, timeout_seconds=timeout_seconds)
        if action == "start":
            result["unit_name"] = "sshd.service"
        return result
    runner = RuntimeSupervisorClientRunner(
        supervisor_socket=SOCKET, supervisor_call=call,
        bridge_call=FakeBridge(), sleep=lambda _seconds: None,
    )
    with pytest.raises(RuntimeSupervisorClientError):
        runner(payload(), 10, lambda: False)
    assert supervisor.lease_state == "stopped"


def test_non_200_inference_is_stopped_and_rejected():
    runner, supervisor, _ = make_runner(bridge=FakeBridge(inference_status=503))
    with pytest.raises(RuntimeSupervisorClientError, match="HTTP 503"):
        runner(payload(), 10, lambda: False)
    assert supervisor.lease_state == "stopped"


def test_empty_smoke_response_is_rejected_and_stopped():
    runner, supervisor, _ = make_runner(bridge=FakeBridge(content=" "))
    with pytest.raises(RuntimeSupervisorClientError, match="exact expected"):
        runner(payload(), 10, lambda: False)
    assert supervisor.lease_state == "stopped"


def test_unconfirmed_stop_is_reported_for_supervisor_recovery():
    supervisor = FakeSupervisor(fail_stop=True)
    runner, _, _ = make_runner(supervisor=supervisor)
    with pytest.raises(RuntimeSupervisorClientError, match="recovery"):
        runner(payload(), 10, lambda: False)


@pytest.mark.parametrize("bad_payload,bad_timeout", [
    ({"id": True, "sha256": SHA}, 10),
    ({"id": 0, "sha256": SHA}, 10),
    ({"id": JOB_ID, "sha256": "bad"}, 10),
    ({"id": JOB_ID, "sha256": SHA}, 0),
    ({"id": JOB_ID, "sha256": SHA}, 1801),
])
def test_rejects_invalid_job_identity_digest_or_timeout(bad_payload, bad_timeout):
    runner, supervisor, bridge = make_runner(bridge=FakeBridge(health_status=503))
    with pytest.raises(ValueError):
        runner(bad_payload, bad_timeout, lambda: False)
    assert supervisor.actions == []
    assert bridge.calls == []


def test_cancellation_during_inference_stops_exact_unit():
    release = threading.Event()
    class BlockingBridge:
        def __init__(self):
            self.calls = []
        def __call__(self, socket_path, method, path, body=b"", *, timeout_seconds):
            self.calls.append((method, path))
            if method == "GET":
                return 200, b"healthy"
            release.wait(2)
            return 200, json.dumps({
                "choices": [{"message": {"content": "READY"}}]
            }).encode()

    bridge = BlockingBridge()
    runner, supervisor, _ = make_runner(bridge=bridge)
    calls = 0
    def cancelled():
        nonlocal calls
        calls += 1
        return calls >= 2
    try:
        with pytest.raises(InterruptedError):
            runner(payload(), 10, cancelled)
    finally:
        release.set()
    assert supervisor.lease_state == "stopped"
    assert [call[1] for call in supervisor.actions] == ["start", "stop"]
    assert bridge.calls == [("GET", "/health"), ("POST", "/v1/chat/completions")]


def test_non_ready_smoke_response_is_rejected_and_stopped():
    runner, supervisor, _ = make_runner(bridge=FakeBridge(content="I am ready."))
    with pytest.raises(RuntimeSupervisorClientError, match="exact expected"):
        runner(payload(), 10, lambda: False)
    assert supervisor.lease_state == "stopped"
