"""API-owned AF_UNIX broker for privileged runtime lease persistence.

The API process owns SQLite writes. The root supervisor receives only a
strict, fixed lease protocol and never opens the application DB read/write.
"""
from __future__ import annotations

import json
import os
import socket
import socketserver
import stat
import struct
import threading
from pathlib import Path

from runtime_supervisor import RuntimeUnitRecord

MAX_REQUEST_BYTES = 4096
MAX_RESPONSE_BYTES = 65536
MAX_RECOVERABLE_RECORDS = 256
_ALLOWED_STATES = {
    "start_pending", "start_uncertain", "active", "stop_pending",
    "recovery_stop_pending", "stopped",
}
_ALLOWED_ACTIONS = {"persist", "set_state", "get", "load_recoverable"}


class RuntimeLeaseBrokerError(RuntimeError):
    """A lease broker request failed or could not be proven complete."""


def _reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise RuntimeLeaseBrokerError("Duplicate JSON key.")
        result[key] = value
    return result


def _record_json(record: RuntimeUnitRecord) -> dict:
    return {"job_id": record.job_id, "unit_name": record.unit_name, "state": record.state}


def _validate_job_id(value) -> int:
    if type(value) is not int or value < 1:
        raise RuntimeLeaseBrokerError("Invalid job ID.")
    return value


def _validate_unit_name(value, job_id: int, unit_namespace: str = "production") -> str:
    if unit_namespace not in {"production", "test-recovery"}:
        raise RuntimeLeaseBrokerError("Unsupported unit namespace.")
    prefix = "ai-chess-arena-runtime-" if unit_namespace == "production" else "ai-chess-arena-runtime-test-"
    expected = f"{prefix}{job_id}.service"
    if not isinstance(value, str) or value != expected:
        raise RuntimeLeaseBrokerError("Unit identity mismatch.")
    return value


def _validate_request(raw: bytes, *, unit_namespace: str = "production") -> dict:
    if not raw or len(raw) > MAX_REQUEST_BYTES or not raw.endswith(b"\n"):
        raise RuntimeLeaseBrokerError("Invalid request framing.")
    try:
        request = json.loads(raw[:-1], object_pairs_hook=_reject_duplicate_keys)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RuntimeLeaseBrokerError("Invalid JSON request.") from exc
    if (not isinstance(request, dict) or not isinstance(request.get("action"), str)
            or request["action"] not in _ALLOWED_ACTIONS):
        raise RuntimeLeaseBrokerError("Unknown broker action.")
    action = request["action"]
    schemas = {
        "persist": {"action", "job_id", "unit_name", "state"},
        "set_state": {"action", "job_id", "unit_name", "state"},
        "get": {"action", "job_id"},
        "load_recoverable": {"action"},
    }
    if set(request) != schemas[action]:
        raise RuntimeLeaseBrokerError("Invalid broker request fields.")
    if action == "load_recoverable":
        return request
    job_id = _validate_job_id(request["job_id"])
    if action in {"persist", "set_state"}:
        _validate_unit_name(request["unit_name"], job_id, unit_namespace)
        if not isinstance(request["state"], str) or request["state"] not in _ALLOWED_STATES:
            raise RuntimeLeaseBrokerError("Invalid lease state.")
        if action == "persist" and request["state"] != "start_pending":
            raise RuntimeLeaseBrokerError("New lease must start pending.")
    return request


def _peer_uid(connection: socket.socket) -> int:
    if not hasattr(socket, "SO_PEERCRED"):
        raise RuntimeLeaseBrokerError("SO_PEERCRED is unavailable.")
    raw = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    _pid, uid, _gid = struct.unpack("3i", raw)
    return uid


class _BrokerHandler(socketserver.StreamRequestHandler):
    def handle(self):
        server = self.server
        try:
            if _peer_uid(self.connection) != server.allowed_uid:
                return
            self.connection.settimeout(server.request_timeout_seconds)
            request = _validate_request(self.rfile.readline(MAX_REQUEST_BYTES + 1), unit_namespace=server.unit_namespace)
            result = server.dispatch(request)
            response = {"ok": True, "result": result}
        except Exception as exc:
            response = {"ok": False, "error": type(exc).__name__}
        try:
            encoded = json.dumps(response, separators=(",", ":")).encode() + b"\n"
            if len(encoded) > MAX_RESPONSE_BYTES:
                encoded = b'{"ok":false,"error":"response_too_large"}\n'
            self.wfile.write(encoded)
        except OSError:
            pass


class RuntimeLeaseBrokerServer:
    """Threaded API-side broker; the supplied store must be API-owned."""

    def __init__(self, socket_path, *, store, allowed_uid: int = 0,
                 socket_mode: int = 0o600, request_timeout_seconds: float = 10.0,
                 unit_namespace: str = "production"):
        if unit_namespace not in {"production", "test-recovery"}:
            raise RuntimeLeaseBrokerError("Unsupported unit namespace.")
        self.unit_namespace = unit_namespace
        self.socket_path = Path(socket_path)
        self.store = store
        self.allowed_uid = allowed_uid
        self.socket_mode = socket_mode
        self.request_timeout_seconds = request_timeout_seconds
        self.ready = threading.Event()
        self._server = None
        if type(allowed_uid) is not int or allowed_uid < 0:
            raise RuntimeLeaseBrokerError("Invalid peer UID.")
        if socket_mode not in {0o600, 0o660} or request_timeout_seconds <= 0:
            raise RuntimeLeaseBrokerError("Invalid broker server policy.")

    def dispatch(self, request: dict):
        action = request["action"]
        if action == "persist":
            record = RuntimeUnitRecord(request["job_id"], request["unit_name"], request["state"])
            self.store.persist(record)
            return {"persisted": True}
        if action == "set_state":
            self.store.set_state(request["job_id"], request["state"], request["unit_name"])
            return {"updated": True}
        if action == "get":
            record = self.store.get(request["job_id"])
            return None if record is None else _record_json(record)
        if action == "load_recoverable":
            records = self.store.load_recoverable()
            if len(records) > MAX_RECOVERABLE_RECORDS:
                raise RuntimeLeaseBrokerError("Recovery lease count exceeds broker bound.")
            return {"complete": True, "records": [_record_json(record) for record in records]}
        raise RuntimeLeaseBrokerError("Unknown broker action.")

    def serve_forever(self):
        if self.socket_path.exists() or self.socket_path.is_symlink():
            raise RuntimeLeaseBrokerError("Broker socket path already exists.")
        try:
            parent_info = self.socket_path.parent.lstat()
        except OSError as exc:
            raise RuntimeLeaseBrokerError("Broker socket parent is not provisioned.") from exc
        if (not stat.S_ISDIR(parent_info.st_mode)
                or parent_info.st_uid != os.geteuid()
                or stat.S_IMODE(parent_info.st_mode) not in {0o700, 0o750}
                or stat.S_IMODE(parent_info.st_mode) & 0o027
                or self.socket_path.parent.resolve(strict=True) != self.socket_path.parent):
            raise RuntimeLeaseBrokerError("Broker socket parent ownership/mode is unsafe.")

        class Server(socketserver.ThreadingUnixStreamServer):
            daemon_threads = False
            block_on_close = True
            allow_reuse_address = False

            def service_actions(server_self):
                # This callback runs only after BaseServer.serve_forever has
                # entered its loop, so shutdown() cannot race its startup.
                self.ready.set()

        self._server = Server(str(self.socket_path), _BrokerHandler, bind_and_activate=False)
        self._server.allowed_uid = self.allowed_uid
        self._server.unit_namespace = self.unit_namespace
        self._server.dispatch = self.dispatch
        self._server.request_timeout_seconds = self.request_timeout_seconds
        bound_socket_identity = None
        try:
            self._server.server_bind()
            bound_info = self.socket_path.lstat()
            if not stat.S_ISSOCK(bound_info.st_mode):
                raise RuntimeLeaseBrokerError("Bound broker path is not a socket.")
            bound_socket_identity = (bound_info.st_dev, bound_info.st_ino)
            os.chmod(self.socket_path, self.socket_mode, follow_symlinks=False)
            self._server.server_activate()
            self._server.serve_forever(poll_interval=0.05)
        finally:
            self.ready.clear()
            self._server.server_close()
            if bound_socket_identity is not None:
                try:
                    current = self.socket_path.lstat()
                    if (stat.S_ISSOCK(current.st_mode)
                            and (current.st_dev, current.st_ino) == bound_socket_identity):
                        self.socket_path.unlink()
                except FileNotFoundError:
                    pass

    def shutdown(self):
        if self._server is not None and self.ready.is_set():
            self._server.shutdown()


class RuntimeLeaseBrokerClient:
    """Lease-store adapter used by the privileged supervisor."""

    def __init__(self, socket_path, *, timeout_seconds: float = 10.0,
                 unit_namespace: str = "production"):
        if unit_namespace not in {"production", "test-recovery"}:
            raise RuntimeLeaseBrokerError("Unsupported unit namespace.")
        self.unit_namespace = unit_namespace
        self.socket_path = str(socket_path)
        self.timeout_seconds = timeout_seconds
        if timeout_seconds <= 0:
            raise RuntimeLeaseBrokerError("Invalid broker client timeout.")

    def _request(self, action: str, **fields):
        request = {"action": action, **fields}
        payload = json.dumps(request, separators=(",", ":")).encode() + b"\n"
        if len(payload) > MAX_REQUEST_BYTES:
            raise RuntimeLeaseBrokerError("Broker request is too large.")
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(self.timeout_seconds)
                client.connect(self.socket_path)
                client.sendall(payload)
                stream = client.makefile("rb")
                try:
                    line = stream.readline(MAX_RESPONSE_BYTES + 1)
                finally:
                    stream.close()
        except OSError as exc:
            raise RuntimeLeaseBrokerError("Lease broker is unavailable.") from exc
        if not line or len(line) > MAX_RESPONSE_BYTES or not line.endswith(b"\n"):
            raise RuntimeLeaseBrokerError("Invalid broker response framing.")
        try:
            response = json.loads(line[:-1], object_pairs_hook=_reject_duplicate_keys)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise RuntimeLeaseBrokerError("Invalid broker response JSON.") from exc
        if not isinstance(response, dict) or response.get("ok") is not True or set(response) != {"ok", "result"}:
            error = response.get("error", "unknown") if isinstance(response, dict) else "unknown"
            raise RuntimeLeaseBrokerError(f"Lease broker rejected request: {error}")
        return response["result"]

    def persist(self, record: RuntimeUnitRecord):
        if not isinstance(record, RuntimeUnitRecord):
            raise RuntimeLeaseBrokerError("Invalid lease record.")
        _validate_job_id(record.job_id)
        _validate_unit_name(record.unit_name, record.job_id, self.unit_namespace)
        if record.state != "start_pending":
            raise RuntimeLeaseBrokerError("New lease must start pending.")
        result = self._request("persist", job_id=record.job_id, unit_name=record.unit_name, state=record.state)
        if result != {"persisted": True}:
            raise RuntimeLeaseBrokerError("Invalid persist acknowledgement.")

    def set_state(self, job_id: int, state: str, unit_name: str | None = None):
        _validate_job_id(job_id)
        if unit_name is None:
            current = self.get(job_id)
            if current is None:
                raise RuntimeLeaseBrokerError("Lease does not exist.")
            unit_name = current.unit_name
        _validate_unit_name(unit_name, job_id, self.unit_namespace)
        if not isinstance(state, str) or state not in _ALLOWED_STATES:
            raise RuntimeLeaseBrokerError("Invalid lease state.")
        result = self._request("set_state", job_id=job_id, unit_name=unit_name, state=state)
        if result != {"updated": True}:
            raise RuntimeLeaseBrokerError("Invalid state-update acknowledgement.")

    def get(self, job_id: int):
        _validate_job_id(job_id)
        result = self._request("get", job_id=job_id)
        if result is None:
            return None
        if not isinstance(result, dict) or set(result) != {"job_id", "unit_name", "state"}:
            raise RuntimeLeaseBrokerError("Invalid lease record response.")
        record = RuntimeUnitRecord(result["job_id"], result["unit_name"], result["state"])
        _validate_job_id(record.job_id)
        _validate_unit_name(record.unit_name, record.job_id, self.unit_namespace)
        if (record.job_id != job_id or not isinstance(record.state, str)
                or record.state not in _ALLOWED_STATES):
            raise RuntimeLeaseBrokerError("Lease record identity mismatch.")
        return record

    def load_recoverable(self):
        result = self._request("load_recoverable")
        if (not isinstance(result, dict) or set(result) != {"complete", "records"}
                or result["complete"] is not True or not isinstance(result["records"], list)
                or len(result["records"]) > MAX_RECOVERABLE_RECORDS):
            raise RuntimeLeaseBrokerError("Incomplete or malformed recovery response.")
        records = []
        for item in result["records"]:
            if not isinstance(item, dict) or set(item) != {"job_id", "unit_name", "state"}:
                raise RuntimeLeaseBrokerError("Malformed recovery lease record.")
            record = RuntimeUnitRecord(item["job_id"], item["unit_name"], item["state"])
            _validate_job_id(record.job_id)
            _validate_unit_name(record.unit_name, record.job_id, self.unit_namespace)
            if (not isinstance(record.state, str)
                    or record.state not in _ALLOWED_STATES - {"stopped"}):
                raise RuntimeLeaseBrokerError("Non-recoverable state in recovery response.")
            records.append(record)
        if len({item.job_id for item in records}) != len(records):
            raise RuntimeLeaseBrokerError("Duplicate job IDs in recovery response.")
        return tuple(records)
