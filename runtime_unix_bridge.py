"""Small, allow-listed AF_UNIX control bridge for an isolated runtime namespace.

The runtime-side bridge forwards only health and one fixed smoke-inference
endpoint to the llamafile's loopback listener. It has no generic proxy mode.
The supervised worker uses this bridge inside each transient systemd unit's private network namespace.
"""
from __future__ import annotations

import base64
import json
import os
import socket
import socketserver
import threading
import urllib.error
import urllib.request
from pathlib import Path

from runtime_socket_handoff import SocketHandoffError, verify_handoff

MAX_REQUEST_BYTES = 2 * 1024 * 1024
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_WIRE_REQUEST_BYTES = ((MAX_REQUEST_BYTES + 2) // 3) * 4 + 4096
MAX_WIRE_RESPONSE_BYTES = ((MAX_RESPONSE_BYTES + 2) // 3) * 4 + 4096
ALLOWED = {("GET", "/health"), ("POST", "/v1/chat/completions")}


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # The bridge must never turn a loopback request into an arbitrary URL.
        return None


def _loopback_only_urlopen(request, *, timeout):
    opener = urllib.request.build_opener(_NoRedirectHandler)
    return opener.open(request, timeout=timeout)


class BridgeError(RuntimeError):
    pass


def _peer_uid(conn: socket.socket) -> int:
    if not hasattr(socket, "SO_PEERCRED"):
        raise BridgeError("SO_PEERCRED niedostępne; most odmawia pracy.")
    raw = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
    _pid, uid, _gid = __import__("struct").unpack("3i", raw)
    return uid


class _Handler(socketserver.StreamRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(self.server.client_timeout_seconds)

    def handle(self):
        bridge = self.server
        if _peer_uid(self.connection) != bridge.allowed_uid:
            return
        try:
            line = self.rfile.readline(MAX_WIRE_REQUEST_BYTES + 1)
        except TimeoutError:
            return
        if not line or len(line) > MAX_WIRE_REQUEST_BYTES:
            return
        # The short client timeout protects request framing; inference may
        # legitimately take much longer, bounded by the systemd job deadline.
        self.connection.settimeout(bridge.response_timeout_seconds)
        try:
            request = json.loads(line)
            method, path = request["method"], request["path"]
            if (method, path) not in ALLOWED:
                raise BridgeError("Endpoint poza allowlist.")
            body = base64.b64decode(request.get("body_b64", ""), validate=True)
            if method == "GET" and body:
                raise BridgeError("GET nie może zawierać body.")
            if len(body) > MAX_REQUEST_BYTES:
                raise BridgeError("Żądanie przekracza limit.")
            headers = {"Content-Type": "application/json"} if method == "POST" else {}
            req = urllib.request.Request(
                f"http://127.0.0.1:{bridge.loopback_port}{path}",
                data=body if method == "POST" else None,
                headers=headers, method=method,
            )
            try:
                with bridge.urlopen(req, timeout=bridge.timeout_seconds) as response:
                    status, payload = response.status, response.read(MAX_RESPONSE_BYTES + 1)
            except urllib.error.HTTPError as exc:
                status, payload = exc.code, exc.read(MAX_RESPONSE_BYTES + 1)
            if len(payload) > MAX_RESPONSE_BYTES:
                raise BridgeError("Odpowiedź przekracza limit.")
            response_obj = {"status": status, "body_b64": base64.b64encode(payload).decode("ascii")}
        except Exception as exc:
            response_obj = {"error": type(exc).__name__}
        encoded = json.dumps(response_obj, separators=(",", ":")).encode() + b"\n"
        self.wfile.write(encoded)


class UnixRuntimeBridge:
    def __init__(self, socket_path: str | Path, *, loopback_port: int,
                 allowed_uid: int, runtime_uid: int | None = None,
                 api_gid: int | None = None,
                 urlopen=_loopback_only_urlopen, timeout_seconds: float = 1800.0,
                 client_timeout_seconds: float = 3.0,
                 response_timeout_seconds: float = 1800.0,
                 cleanup_socket: bool = True):
        self.socket_path = Path(socket_path)
        self.loopback_port = int(loopback_port)
        self.allowed_uid = int(allowed_uid)
        self.runtime_uid = os.getuid() if runtime_uid is None else int(runtime_uid)
        self.api_gid = os.getgid() if api_gid is None else int(api_gid)
        self.urlopen = urlopen
        self.timeout_seconds = float(timeout_seconds)
        self.client_timeout_seconds = float(client_timeout_seconds)
        self.response_timeout_seconds = float(response_timeout_seconds)
        if type(cleanup_socket) is not bool:
            raise BridgeError("Invalid socket cleanup policy.")
        self.cleanup_socket = cleanup_socket
        self.ready = threading.Event()
        if not 1 <= self.loopback_port <= 65535:
            raise BridgeError("Nieprawidłowy port loopback.")
        if min(self.allowed_uid, self.runtime_uid, self.api_gid) < 0:
            raise BridgeError("Nieprawidłowa tożsamość procesu.")
        if (self.timeout_seconds <= 0 or self.client_timeout_seconds <= 0
                or self.response_timeout_seconds <= 0):
            raise BridgeError("Timeout mostu musi być dodatni.")
        self._server = None

    def serve_forever(self):
        try:
            verify_handoff(
                self.socket_path.parent, self.socket_path,
                runtime_uid=self.runtime_uid, api_gid=self.api_gid,
                require_socket=False,
            )
        except SocketHandoffError as exc:
            raise BridgeError(str(exc)) from exc
        class Server(socketserver.UnixStreamServer):
            allow_reuse_address = False

            def service_actions(server_self):
                # Signal only after serve_forever has entered its accept loop.
                self.ready.set()
        self._server = Server(str(self.socket_path), _Handler)
        self._server.allowed_uid = self.allowed_uid
        self._server.loopback_port = self.loopback_port
        self._server.urlopen = self.urlopen
        self._server.timeout_seconds = self.timeout_seconds
        self._server.client_timeout_seconds = self.client_timeout_seconds
        self._server.response_timeout_seconds = self.response_timeout_seconds
        try:
            os.chmod(self.socket_path, 0o660)
            try:
                verify_handoff(
                    self.socket_path.parent, self.socket_path,
                    runtime_uid=self.runtime_uid, api_gid=self.api_gid,
                )
            except SocketHandoffError as exc:
                raise BridgeError(str(exc)) from exc
            self._server.serve_forever(poll_interval=0.05)
        finally:
            self.ready.clear()
            self._server.server_close()
            if self.cleanup_socket:
                try:
                    self.socket_path.unlink()
                except FileNotFoundError:
                    pass

    def shutdown(self):
        if self._server is not None and self.ready.is_set():
            self._server.shutdown()


def unix_request(socket_path: str | Path, method: str, path: str, body: bytes = b"",
                 *, timeout_seconds: float = 3.0) -> tuple[int, bytes]:
    if (method, path) not in ALLOWED:
        raise BridgeError("Endpoint poza allowlist.")
    if len(body) > MAX_REQUEST_BYTES:
        raise BridgeError("Żądanie przekracza limit.")
    payload = json.dumps({
        "method": method, "path": path,
        "body_b64": base64.b64encode(body).decode("ascii"),
    }, separators=(",", ":")).encode() + b"\n"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(timeout_seconds)
        client.connect(str(socket_path))
        client.sendall(payload)
        stream = client.makefile("rb")
        try:
            line = stream.readline(MAX_WIRE_RESPONSE_BYTES + 1)
        except OSError as exc:
            raise BridgeError("Most odrzucił połączenie.") from exc
    if not line:
        raise BridgeError("Most odrzucił połączenie.")
    try:
        result = json.loads(line)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise BridgeError("Most odrzucił połączenie lub nie zwrócił poprawnej odpowiedzi.") from exc
    if "error" in result:
        raise BridgeError(f"Most odrzucił żądanie: {result['error']}")
    response = base64.b64decode(result["body_b64"], validate=True)
    if len(response) > MAX_RESPONSE_BYTES:
        raise BridgeError("Odpowiedź przekracza limit.")
    return int(result["status"]), response
