import json
import os
import socket
import threading

import pytest

from runtime_supervisor_ipc import (
    MAX_REQUEST_BYTES,
    RuntimeSupervisorIPCServer,
    SupervisorIPCError,
    _remove_stale_unix_socket,
    _validate_request,
    supervisor_request,
)


def test_request_schema_is_job_id_only():
    assert _validate_request(b'{"action":"status","job_id":7}\n') == {
        "action": "status", "job_id": 7
    }
    assert _validate_request(b'{"action":"recover"}\n') == {"action": "recover"}
    for payload in (
        b'{"action":"start","job_id":7,"argv":["sh"]}\n',
        b'{"action":"exec","job_id":7}\n',
        b'{"action":[],"job_id":7}\n',
        b'{"action":"start","action":"stop","job_id":7}\n',
        b'{"action":"start","job_id":true}\n',
        b'{"action":"start","job_id":0}\n',
        b'{"action":"start","job_id":7}',
        b'{"action":"recover","job_id":7}\n',
        b'{"action":"recover","extra":true}\n',
        b'x' * (MAX_REQUEST_BYTES + 1),
    ):
        with pytest.raises(SupervisorIPCError):
            _validate_request(payload)


def test_client_rejects_arbitrary_action_and_invalid_job_id(tmp_path):
    with pytest.raises(SupervisorIPCError):
        supervisor_request(tmp_path / "missing.sock", "exec", 1)
    with pytest.raises(SupervisorIPCError):
        supervisor_request(tmp_path / "missing.sock", "start", True)
    with pytest.raises(SupervisorIPCError):
        supervisor_request(tmp_path / "missing.sock", "recover", 1)


def test_server_refuses_existing_socket_or_symlink(tmp_path):
    path = tmp_path / "supervisor.sock"
    path.touch()
    with pytest.raises(SupervisorIPCError, match="już istnieje"):
        RuntimeSupervisorIPCServer(path, allowed_uid=os.getuid(),
                                   start=lambda _: None, stop=lambda _: None,
                                   status=lambda _: None).serve_forever()


def test_server_recovers_only_a_stale_owned_socket(tmp_path):
    path = tmp_path / "supervisor.sock"
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(str(path))
    stale.close()  # equivalent to a process dying without unlinking its socket
    os.chmod(path, 0o660)
    server = RuntimeSupervisorIPCServer(
        path, allowed_uid=os.getuid(),
        start=lambda job_id: {"job_id": job_id},
        stop=lambda job_id: {"job_id": job_id},
        status=lambda job_id: {"job_id": job_id},
        socket_gid=os.getgid(),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        assert server.ready.wait(timeout=2), "supervisor IPC did not recover stale socket"
        assert supervisor_request(path, "status", 910000001) == {"job_id": 910000001}
    finally:
        server.shutdown()
        thread.join(timeout=2)
    assert not path.exists()


def test_stale_socket_cleanup_refuses_live_listener_and_foreign_path(tmp_path):
    path = tmp_path / "live.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(path))
    listener.listen(1)
    os.chmod(path, 0o660)
    try:
        with pytest.raises(SupervisorIPCError, match="live listener"):
            _remove_stale_unix_socket(
                path, expected_uid=os.getuid(), expected_gid=os.getgid(),
                expected_mode=0o660,
            )
        assert path.exists()
    finally:
        listener.close()
        path.unlink(missing_ok=True)

    path.write_text("not a socket", encoding="utf-8")
    with pytest.raises(SupervisorIPCError, match="niebezpieczną tożsamością"):
        _remove_stale_unix_socket(
            path, expected_uid=os.getuid(), expected_gid=os.getgid(),
            expected_mode=0o660,
        )
    assert path.read_text(encoding="utf-8") == "not a socket"
    path.unlink()
    target = tmp_path / "target"
    target.touch()
    path.symlink_to(target)
    with pytest.raises(SupervisorIPCError, match="niebezpieczną tożsamością"):
        _remove_stale_unix_socket(
            path, expected_uid=os.getuid(), expected_gid=os.getgid(),
            expected_mode=0o660,
        )
    assert path.is_symlink() and target.exists()

def test_round_trip_calls_only_injected_job_handlers(tmp_path):
    path = tmp_path / "supervisor.sock"
    calls = []
    server = RuntimeSupervisorIPCServer(
        path, allowed_uid=os.getuid(),
        start=lambda job_id: calls.append(("start", job_id)) or {"accepted": True},
        stop=lambda job_id: calls.append(("stop", job_id)) or {"stopped": True},
        status=lambda job_id: calls.append(("status", job_id)) or {"state": "idle"},
        recover=lambda: calls.append(("recover", None)) or {"count": 2},
        socket_gid=os.getgid(),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        assert server.ready.wait(timeout=2), "supervisor IPC did not become ready"
        assert path.stat().st_gid == os.getgid()
        assert (path.stat().st_mode & 0o777) == 0o660
        assert supervisor_request(path, "start", 13) == {"accepted": True}
        assert supervisor_request(path, "status", 13) == {"state": "idle"}
        assert supervisor_request(path, "recover") == {"count": 2}
        assert calls == [("start", 13), ("status", 13), ("recover", None)]
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
        assert server.ready.wait(timeout=2), "supervisor IPC did not become ready"
        with pytest.raises(SupervisorIPCError, match="RuntimeError") as exc:
            supervisor_request(path, "start", 4)
        assert "/etc/private" not in str(exc.value)
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_server_rejects_peer_uid_outside_allowlist(tmp_path):
    path = tmp_path / "supervisor.sock"
    calls = []
    server = RuntimeSupervisorIPCServer(
        path, allowed_uid=os.getuid() + 1,
        start=lambda job_id: calls.append(("start", job_id)) or {"accepted": True},
        stop=lambda _: None, status=lambda _: None,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        assert server.ready.wait(timeout=2), "supervisor IPC did not become ready"
        with pytest.raises(SupervisorIPCError):
            supervisor_request(path, "start", 2)
        # An unauthorised peer is closed without a response; its injected
        # handlers must never run, regardless of send/close scheduling.
        assert calls == []
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
