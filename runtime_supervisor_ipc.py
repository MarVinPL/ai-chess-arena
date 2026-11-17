"""Narrow AF_UNIX API-to-supervisor protocol for runtime job control.

The unprivileged API may identify a job, but cannot supply argv, paths,
systemd properties, environment variables, or unit names. The privileged
supervisor must resolve the job from its trusted store and revalidate all
permissions/artifact pins before acting. No socket is created at import time.
"""
from __future__ import annotations

import json
import os
import socket
import socketserver
import struct
import threading
from pathlib import Path
from typing import Callable

MAX_REQUEST_BYTES = 4096
MAX_RESPONSE_BYTES = 4096
_ALLOWED_ACTIONS = {"start", "stop", "status", "recover"}


class SupervisorIPCError(RuntimeError):
    pass


def _peer_credentials(conn: socket.socket) -> tuple[int, int, int]:
    if not hasattr(socket, "SO_PEERCRED"):
        raise SupervisorIPCError("SO_PEERCRED niedostępne; IPC odmawia pracy.")
    raw = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    return struct.unpack("3i", raw)


def _validate_request(raw: bytes) -> dict:
    if not raw or len(raw) > MAX_REQUEST_BYTES or not raw.endswith(b"\n"):
        raise SupervisorIPCError("Nieprawidłowy rozmiar lub framing żądania.")
    def reject_duplicate_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise SupervisorIPCError("Powtórzone pole JSON.")
            result[key] = value
        return result

    try:
        request = json.loads(raw[:-1], object_pairs_hook=reject_duplicate_keys)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise SupervisorIPCError("Niepoprawny JSON żądania.") from exc
    if not isinstance(request, dict):
        raise SupervisorIPCError("Żądanie musi być obiektem JSON.")
    if not isinstance(request.get("action"), str) or request["action"] not in _ALLOWED_ACTIONS:
        raise SupervisorIPCError("Akcja poza allowlist.")
    if request["action"] == "recover":
        if set(request) != {"action"}:
            raise SupervisorIPCError("Odzyskiwanie nie przyjmuje identyfikatora ani innych pól.")
        return request
    if set(request) != {"action", "job_id"}:
        raise SupervisorIPCError("Żądanie zawiera niedozwolone pola.")
    if type(request["job_id"]) is not int or request["job_id"] <= 0:
        raise SupervisorIPCError("Nieprawidłowy identyfikator zadania.")
    return request


class _Handler(socketserver.StreamRequestHandler):
    def handle(self):
        server = self.server
        # Reject unauthorised peers before reading or replying. A peer can be
        # closed immediately after connect; the client may observe EOF/BrokenPipe
        # depending on scheduling, but no handler or privileged operation runs.
        try:
            _pid, uid, _gid = _peer_credentials(self.connection)
        except Exception:
            return
        if uid != server.allowed_uid:
            return
        try:
            self.connection.settimeout(server.timeout_seconds)
            raw = self.rfile.readline(MAX_REQUEST_BYTES + 1)
            request = _validate_request(raw)
            # A start handler may hash/copy a large approved artifact. Keep the
            # request-read timeout short but allow a bounded long response.
            self.connection.settimeout(server.response_timeout_seconds)
            handler = server.handlers[request["action"]]
            if not callable(handler):
                raise SupervisorIPCError("Akcja nie jest skonfigurowana.")
            result = handler() if request["action"] == "recover" else handler(request["job_id"])
            response = {"ok": True, "result": result}
        except Exception as exc:
            # Do not leak exception text, paths, DB data or command output.
            response = {"ok": False, "error": type(exc).__name__}
        encoded = json.dumps(response, separators=(",", ":")).encode() + b"\n"
        if len(encoded) > MAX_RESPONSE_BYTES:
            encoded = b'{"ok":false,"error":"response_too_large"}\n'
        try:
            self.wfile.write(encoded)
        except OSError:
            pass


class RuntimeSupervisorIPCServer:
    def __init__(self, socket_path: str | Path, *, allowed_uid: int,
                 start: Callable[[int], object], stop: Callable[[int], object],
                 status: Callable[[int], object], recover: Callable[[], object] | None = None,
                 timeout_seconds: float = 3.0,
                 response_timeout_seconds: float = 1800.0,
                 socket_mode: int = 0o660, socket_gid: int | None = None):
        self.socket_path = Path(socket_path)
        self.allowed_uid = int(allowed_uid)
        self.handlers = {"start": start, "stop": stop, "status": status}
        if recover is not None:
            if not callable(recover):
                raise SupervisorIPCError("Handler odzyskiwania musi być wywoływalny.")
            self.handlers["recover"] = recover
        self.timeout_seconds = float(timeout_seconds)
        self.response_timeout_seconds = float(response_timeout_seconds)
        self.socket_mode = int(socket_mode)
        self.socket_gid = None if socket_gid is None else int(socket_gid)
        if (self.allowed_uid < 0 or (self.socket_gid is not None and self.socket_gid < 0)
                or self.timeout_seconds <= 0
                or self.response_timeout_seconds <= 0):
            raise SupervisorIPCError("Nieprawidłowa konfiguracja IPC.")
        if self.socket_mode not in {0o600, 0o660}:
            raise SupervisorIPCError("Tryb socketu poza dozwoloną listą.")
        if not all(callable(fn) for fn in self.handlers.values()):
            raise SupervisorIPCError("Wszystkie handlery muszą być jawnie skonfigurowane.")
        self._server = None
        # Set only after the socket is bound, permissions are applied and the
        # listener is active. Tests/owners can wait for real readiness rather
        # than inferring it from the filesystem entry appearing.
        self.ready = threading.Event()

    def serve_forever(self):
        if self.socket_path.exists() or self.socket_path.is_symlink():
            raise SupervisorIPCError("Socket path już istnieje; odmowa nadpisania.")
        if not self.socket_path.parent.is_dir():
            raise SupervisorIPCError("Katalog socketu musi być wcześniej przygotowany przez administratora.")

        class Server(socketserver.UnixStreamServer):
            allow_reuse_address = False

        # Delay bind/listen until all handler state is installed. Otherwise a
        # client can connect in the constructor-to-configuration window and
        # race the first request against missing server attributes.
        self._server = Server(str(self.socket_path), _Handler, bind_and_activate=False)
        self._server.allowed_uid = self.allowed_uid
        self._server.handlers = self.handlers
        self._server.timeout_seconds = self.timeout_seconds
        self._server.response_timeout_seconds = self.response_timeout_seconds
        try:
            self._server.server_bind()
            if self.socket_gid is not None:
                os.chown(self.socket_path, -1, self.socket_gid, follow_symlinks=False)
            os.chmod(self.socket_path, self.socket_mode, follow_symlinks=False)
            self._server.server_activate()
            self.ready.set()
            self._server.serve_forever(poll_interval=0.1)
        finally:
            self.ready.clear()
            self._server.server_close()
            try:
                self.socket_path.unlink()
            except FileNotFoundError:
                pass

    def shutdown(self):
        if self._server is not None:
            self._server.shutdown()


def supervisor_request(socket_path: str | Path, action: str, job_id: int | None = None,
                       *, timeout_seconds: float = 3.0) -> object:
    if action not in _ALLOWED_ACTIONS:
        raise SupervisorIPCError("Nieprawidłowe żądanie supervisor IPC.")
    if action == "recover":
        if job_id is not None:
            raise SupervisorIPCError("Odzyskiwanie nie przyjmuje identyfikatora zadania.")
        request = {"action": action}
    else:
        if type(job_id) is not int or job_id <= 0:
            raise SupervisorIPCError("Nieprawidłowe żądanie supervisor IPC.")
        request = {"action": action, "job_id": job_id}
    if timeout_seconds <= 0:
        raise SupervisorIPCError("Timeout musi być dodatni.")
    payload = json.dumps(request, separators=(",", ":")).encode() + b"\n"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(timeout_seconds)
        try:
            client.connect(str(socket_path))
            client.sendall(payload)
            stream = client.makefile("rb")
            try:
                line = stream.readline(MAX_RESPONSE_BYTES + 1)
            finally:
                stream.close()
        except OSError as exc:
            raise SupervisorIPCError("Nie można połączyć się z supervisorem.") from exc
    if not line or len(line) > MAX_RESPONSE_BYTES or not line.endswith(b"\n"):
        raise SupervisorIPCError("Supervisor zwrócił nieprawidłową odpowiedź.")
    try:
        response = json.loads(line[:-1])
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise SupervisorIPCError("Supervisor zwrócił niepoprawny JSON.") from exc
    if not isinstance(response, dict) or response.get("ok") is not True:
        error = response.get("error", "unknown") if isinstance(response, dict) else "unknown"
        raise SupervisorIPCError(f"Supervisor odrzucił żądanie: {error}")
    return response.get("result")
