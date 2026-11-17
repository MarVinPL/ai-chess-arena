import json
import os
import socket
import threading
import time

import pytest

from runtime_supervisor_ipc import (
    MAX_REQUEST_BYTES,
    RuntimeSupervisorIPCServer,
    SupervisorIPCError,
    _validate_request,
    supervisor_request,
)


def test_request_schema_is_job_id_only():
    assert _validate_request(b'{"action":"status","job_id":7}\n') == {
        "action": "status", "job_id": 7
    }
    for payload in (
        b'{"action":"start","job_id":7,"argv":["sh"]}\n',
        b'{"action":"exec","job_id":7}\n',
        b'{"action":[],"job_id":7}\n',
        b'{"action":"start","action":"stop","job_id":7}\n',
        b'{"action":"start","job_id":true}\n',
        b'{"action":"start","job_id":0}\n',
        b'{"action":"start","job_id":7}',
        b'x' * (MAX_REQUEST_BYTES + 1),
    ):
        with pytest.raises(SupervisorIPCError):
            _validate_request(payload)


def test_client_rejects_arbitrary_action_and_invalid_job_id(tmp_path):
    with pytest.raises(SupervisorIPCError):
        supervisor_request(tmp_path / "missing.sock", "exec", 1)
    with pytest.raises(SupervisorIPCError):
        supervisor_request(tmp_path / "missing.sock", "start", True)


def test_server_refuses_existing_socket_or_symlink(tmp_path):
    path = tmp_path / "supervisor.sock"
    path.touch()
    with pytest.raises(SupervisorIPCError, match="już istnieje"):
        RuntimeSupervisorIPCServer(path, allowed_uid=os.getuid(),
                                   start=lambda _: None, stop=lambda _: None,
                                   status=lambda _: None).serve_forever()


def test_round_trip_calls_only_injected_job_handlers(tmp_path):
    path = tmp_path / "supervisor.sock"
    calls = []
    server = RuntimeSupervisorIPCServer(
        path, allowed_uid=os.getuid(),
        start=lambda job_id: calls.append(("start", job_id)) or {"accepted": True},
        stop=lambda job_id: calls.append(("stop", job_id)) or {"stopped": True},
        status=lambda job_id: calls.append(("status", job_id)) or {"state": "idle"},
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 2
        while not path.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert path.exists()
        assert supervisor_request(path, "start", 13) == {"accepted": True}
        assert supervisor_request(path, "status", 13) == {"state": "idle"}
        assert calls == [("start", 13), ("status", 13)]
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_handler_exception_is_sanitized(tmp_path):
    path = tmp_path / "supervisor.sock"
    server = RuntimeSupervisorIPCServer(
        path, allowed_uid=os.getuid(),
        start=lambda _: (_ for _ in ()).throw(RuntimeError("secret path /etc/private")),
        stop=lambda _: None, status=lambda _: None,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 2
        while not path.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        with pytest.raises(SupervisorIPCError, match="RuntimeError") as exc:
            supervisor_request(path, "start", 4)
        assert "/etc/private" not in str(exc.value)
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_server_rejects_peer_uid_outside_allowlist(tmp_path):
    path = tmp_path / "supervisor.sock"
    server = RuntimeSupervisorIPCServer(
        path, allowed_uid=os.getuid() + 1,
        start=lambda _: {"accepted": True}, stop=lambda _: None, status=lambda _: None,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 2
        while not path.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        with pytest.raises(SupervisorIPCError, match="Supervisor odrzucił"):
            supervisor_request(path, "start", 2)
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_long_handler_response_timeout_is_separate_from_short_request_timeout(tmp_path):
    server = RuntimeSupervisorIPCServer(
        tmp_path / "supervisor.sock", allowed_uid=os.getuid(),
        start=lambda job_id: {"job_id": job_id},
        stop=lambda job_id: {"job_id": job_id},
        status=lambda job_id: {"job_id": job_id},
        timeout_seconds=3.0,
        response_timeout_seconds=1800.0,
    )
    assert server.timeout_seconds == 3.0
    assert server.response_timeout_seconds == 1800.0
