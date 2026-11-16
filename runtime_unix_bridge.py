"""Small, allow-listed AF_UNIX control bridge for an isolated runtime namespace.

The runtime-side bridge forwards only health and one fixed smoke-inference
endpoint to the llamafile's loopback listener. It has no generic proxy mode.
This module is a prototype and is not wired to the executor or systemd.
"""
from __future__ import annotations

import base64
import json
import os
import socket
import socketserver
import urllib.error
import urllib.request
from pathlib import Path

MAX_REQUEST_BYTES = 2 * 1024 * 1024
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
ALLOWED = {("GET", "/health"), ("POST", "/v1/chat/completions")}


class BridgeError(RuntimeError):
    pass


def _peer_uid(conn: socket.socket) -> int:
    if not hasattr(socket, "SO_PEERCRED"):
        raise BridgeError("SO_PEERCRED niedostępne; most odmawia pracy.")
    raw = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
    _pid, uid, _gid = __import__("struct").unpack("3i", raw)
    return uid


class _Handler(socketserver.StreamRequestHandler):
    def handle(self):
        bridge = self.server
        if _peer_uid(self.connection) != bridge.allowed_uid:
            return
        line = self.rfile.readline(MAX_REQUEST_BYTES + 1)
        if not line or len(line) > MAX_REQUEST_BYTES:
            return
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
                 allowed_uid: int, urlopen=urllib.request.urlopen,
                 timeout_seconds: float = 2.0):
        self.socket_path = Path(socket_path)
        self.loopback_port = int(loopback_port)
        self.allowed_uid = int(allowed_uid)
        self.urlopen = urlopen
        self.timeout_seconds = float(timeout_seconds)
        self._server = None

    def serve_forever(self):
        if not self.socket_path.parent.is_dir():
            raise BridgeError("Katalog gniazda musi być wcześniej przygotowany przez supervisor.")
        if self.socket_path.exists():
            raise BridgeError("Ścieżka gniazda już istnieje; odmowa nadpisania.")
        class Server(socketserver.UnixStreamServer):
            allow_reuse_address = False
        self._server = Server(str(self.socket_path), _Handler)
        self._server.allowed_uid = self.allowed_uid
        self._server.loopback_port = self.loopback_port
        self._server.urlopen = self.urlopen
        self._server.timeout_seconds = self.timeout_seconds
        try:
            os.chmod(self.socket_path, 0o660)
            self._server.serve_forever(poll_interval=0.1)
        finally:
            self._server.server_close()
            try:
                self.socket_path.unlink()
            except FileNotFoundError:
                pass

    def shutdown(self):
        if self._server is not None:
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
            line = stream.readline(MAX_RESPONSE_BYTES * 2 + 1024)
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
