"""Opt-in loopback-only HTTP adapter for authenticated remote result intake.

No listener is started by importing this module. A reverse proxy must enforce
TLS and network access controls before any deployment.
"""
import json
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock

from remote_worker_authenticated_ingest import AuthenticatedResultIngest

MAX_BODY = 2048


class RateLimiter:
    def __init__(self, limit=20, window=60, clock=time.monotonic):
        self.limit, self.window, self.clock = limit, window, clock
        self.events = {}
        self.lock = Lock()

    def allow(self, peer):
        now = self.clock()
        with self.lock:
            events = self.events.setdefault(peer, deque())
            while events and events[0] <= now - self.window:
                events.popleft()
            if len(events) >= self.limit:
                return False
            events.append(now)
            return True


def make_handler(db_path, *, limiter=None):
    limiter = limiter or RateLimiter()
    ingest = AuthenticatedResultIngest(db_path)

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            if self.path != '/internal/remote-worker/result':
                return self.reply(404)
            peer = self.client_address[0]
            if not limiter.allow(peer):
                return self.reply(429)
            length_header = self.headers.get('Content-Length')
            if self.headers.get('Transfer-Encoding') or not length_header or not length_header.isascii() or not length_header.isdecimal():
                return self.reply(400)
            length = int(length_header)
            if not 0 < length <= MAX_BODY:
                return self.reply(413)
            if self.headers.get('Content-Type', '').split(';', 1)[0].strip().lower() != 'application/json':
                return self.reply(415)
            try:
                body = json.loads(self.rfile.read(length).decode('utf-8'))
                if not isinstance(body, dict) or set(body) != {'worker_id', 'token', 'result'}:
                    raise ValueError('Invalid payload')
                ingest.accept(body['worker_id'], body['token'], body['result'])
            except (ValueError, TypeError, UnicodeError, json.JSONDecodeError):
                # Never disclose worker existence, token validity, job IDs or FEN.
                return self.reply(401)
            except Exception:
                return self.reply(503)
            return self.reply(202)

        def reply(self, status):
            payload = json.dumps({'accepted': status == 202}).encode('ascii')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format, *args):
            pass  # Do not log secrets or raw worker requests.

    return Handler


def create_loopback_server(db_path, *, port=0, limiter=None):
    if type(port) is not int or not 0 <= port <= 65535:
        raise ValueError('Invalid port')
    return ThreadingHTTPServer(('127.0.0.1', port), make_handler(db_path, limiter=limiter))
