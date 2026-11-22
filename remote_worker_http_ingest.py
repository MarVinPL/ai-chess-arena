"""Opt-in loopback-only HTTP adapter for authenticated remote result intake.

No listener is started by importing this module. A reverse proxy must enforce
TLS and network access controls before any deployment.
"""
import json
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock

MAX_PEERS = 1024
READ_TIMEOUT = 5

from remote_worker_authenticated_ingest import AuthenticatedResultIngest
from remote_worker_job_delivery import AuthenticatedJobDelivery

MAX_BODY = 2048


class RateLimiter:
    def __init__(self, limit=20, window=60, clock=time.monotonic, max_peers=MAX_PEERS):
        self.limit, self.window, self.clock = limit, window, clock
        self.max_peers = max_peers
        self.events = {}
        self.lock = Lock()

    def allow(self, peer):
        now = self.clock()
        with self.lock:
            # Evict expired peers, preventing unbounded state growth under
            # many unique source addresses.
            for key, history in list(self.events.items()):
                while history and history[0] <= now - self.window:
                    history.popleft()
                if not history:
                    del self.events[key]
            if peer not in self.events and len(self.events) >= self.max_peers:
                return False
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
    delivery = AuthenticatedJobDelivery(db_path)

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(READ_TIMEOUT)

        def do_POST(self):
            if self.path not in ('/internal/remote-worker/result', '/internal/remote-worker/job'):
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
                if not isinstance(body, dict):
                    raise ValueError('Invalid payload')
                if self.path == '/internal/remote-worker/job':
                    if set(body) != {'worker_id', 'token'}:
                        raise ValueError('Invalid payload')
                    job = delivery.retrieve(body['worker_id'], body['token'])
                    return self.reply(200, {'job': job})
                if set(body) != {'worker_id', 'token', 'result'}:
                    raise ValueError('Invalid payload')
                ingest.accept(body['worker_id'], body['token'], body['result'])
            except (ValueError, TypeError, UnicodeError, json.JSONDecodeError):
                # Never disclose worker existence, token validity, job IDs or FEN.
                return self.reply(401)
            except (TimeoutError, OSError):
                # A stalled client cannot occupy a handler indefinitely.
                return
            except Exception:
                return self.reply(503)
            return self.reply(202)

        def reply(self, status, data=None):
            payload = json.dumps(data if data is not None else {'accepted': status == 202}).encode('ascii')
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
