import base64
import json
import os
import threading
import time
import urllib.error

import pytest

from runtime_unix_bridge import BridgeError, UnixRuntimeBridge, unix_request


class FakeResponse:
    def __init__(self, status, body):
        self.status, self.body = status, body
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def read(self, limit=-1): return self.body[:limit]


def start_bridge(tmp_path, urlopen=None, allowed_uid=None):
    path = tmp_path / "runtime.sock"
    bridge = UnixRuntimeBridge(
        path, loopback_port=19090, allowed_uid=allowed_uid if allowed_uid is not None else os.getuid(),
        urlopen=urlopen or (lambda req, timeout: FakeResponse(200, b'{"ok":true}')),
    )
    thread = threading.Thread(target=bridge.serve_forever, daemon=True)
    thread.start()
    deadline = time.monotonic() + 2
    while not path.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert path.exists()
    return bridge, thread, path


def stop_bridge(bridge, thread):
    bridge.shutdown()
    thread.join(timeout=2)


def test_unix_bridge_forwards_health_only_to_loopback(tmp_path):
    seen = []
    def fake(req, timeout):
        seen.append((req.full_url, req.get_method(), timeout))
        return FakeResponse(200, b"healthy")
    bridge, thread, path = start_bridge(tmp_path, fake)
    try:
        status, body = unix_request(path, "GET", "/health")
        assert (status, body) == (200, b"healthy")
        assert seen == [("http://127.0.0.1:19090/health", "GET", 2.0)]
    finally:
        stop_bridge(bridge, thread)


def test_unix_bridge_forwards_fixed_smoke_inference(tmp_path):
    seen = []
    def fake(req, timeout):
        seen.append((req.full_url, req.get_method(), req.data))
        return FakeResponse(200, b'{"choices":[]}')
    bridge, thread, path = start_bridge(tmp_path, fake)
    body = b'{"messages":[{"role":"user","content":"READY"}]}'
    try:
        status, response = unix_request(path, "POST", "/v1/chat/completions", body)
        assert status == 200
        assert response == b'{"choices":[]}'
        assert seen == [("http://127.0.0.1:19090/v1/chat/completions", "POST", body)]
    finally:
        stop_bridge(bridge, thread)


@pytest.mark.parametrize("method,path", [
    ("GET", "/"), ("POST", "/v1/models"), ("DELETE", "/health"),
    ("GET", "http://example.com/"),
])
def test_unix_client_rejects_non_allowlisted_paths(tmp_path, method, path):
    with pytest.raises(BridgeError):
        unix_request(tmp_path / "missing.sock", method, path)


def test_bridge_refuses_existing_socket_path(tmp_path):
    path = tmp_path / "already.sock"
    path.touch()
    bridge = UnixRuntimeBridge(path, loopback_port=19090, allowed_uid=os.getuid())
    with pytest.raises(BridgeError):
        bridge.serve_forever()


def test_bridge_refuses_unprepared_parent_directory(tmp_path):
    bridge = UnixRuntimeBridge(tmp_path / "missing" / "runtime.sock",
                               loopback_port=19090, allowed_uid=os.getuid())
    with pytest.raises(BridgeError):
        bridge.serve_forever()


def test_bridge_rejects_wrong_peer_uid(tmp_path):
    bridge, thread, path = start_bridge(tmp_path, allowed_uid=os.getuid() + 1)
    try:
        with pytest.raises(BridgeError):
            unix_request(path, "GET", "/health")
    finally:
        stop_bridge(bridge, thread)


def test_bridge_handles_upstream_http_error(tmp_path):
    def fake(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 503, "unavailable", {}, None)
    bridge, thread, path = start_bridge(tmp_path, fake)
    try:
        status, body = unix_request(path, "GET", "/health")
        assert status == 503
        assert body == b""
    finally:
        stop_bridge(bridge, thread)
