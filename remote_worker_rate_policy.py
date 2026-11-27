"""Bounded, process-local admission limits for Worker HTTP requests.

Use the actual socket peer, never untrusted forwarding headers. Behind a
reverse proxy the peer budget is shared; tune only after measured load.
Admission before parsing limits malformed requests and rotating worker IDs.
Identity admission follows successful machine authentication, before effects.
"""
import time
from remote_worker_http_ingest import RateLimiter

# Requests per rolling 60 seconds. Keep the existing job-preview limit.
ROUTE_LIMITS = {
    'authenticate': 12, 'job': 12, 'update-safety': 12,
    'job-poll': 120, 'ready': 12, 'job-result': 30,
    'update-pause-poll': 120, 'board-view': 120,
}


class WorkerRatePolicy:
    def __init__(self, *, clock=time.monotonic, max_peers=1024):
        options = dict(clock=clock, max_peers=max_peers, window=60)
        self.peer = RateLimiter(limit=600, **options)
        self.identity = {route: RateLimiter(limit=limit, **options)
                         for route, limit in ROUTE_LIMITS.items()}
        self.route_peer = {route: RateLimiter(limit=limit * 10, **options)
                           for route, limit in ROUTE_LIMITS.items()}

    def admit_peer(self, route, peer):
        if route not in self.route_peer or not isinstance(peer, str) or not peer:
            return False
        return self.peer.allow(peer) and self.route_peer[route].allow(peer)

    def admit_authenticated(self, route, worker_id):
        if route not in self.identity or not isinstance(worker_id, str) or not worker_id:
            return False
        return self.identity[route].allow(worker_id)
