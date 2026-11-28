import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import server


def test_http_listener_exits_promptly_after_shutdown_request():
    http_server = ThreadingHTTPServer(
        ("127.0.0.1", 0), BaseHTTPRequestHandler,
    )
    shutdown_requested = threading.Event()
    trigger = threading.Thread(
        target=lambda: (time.sleep(0.05), shutdown_requested.set()),
        daemon=True,
    )
    trigger.start()
    server._serve_http_until_shutdown(http_server, shutdown_requested)
    trigger.join(1)
    assert not trigger.is_alive()
    assert http_server.fileno() == -1


def test_http_listener_does_not_enter_accept_loop_when_already_stopping():
    http_server = ThreadingHTTPServer(
        ("127.0.0.1", 0), BaseHTTPRequestHandler,
    )
    shutdown_requested = threading.Event()
    shutdown_requested.set()
    server._serve_http_until_shutdown(http_server, shutdown_requested)
    assert http_server.fileno() == -1
