"""Coordinator-side, in-memory pairing approvals for Issue #192.

No listener or worker access is enabled by importing this module. Integrators
must authenticate the administrator before calling approve/revoke.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import threading
import time
from dataclasses import dataclass

_CODE = re.compile(r'[A-Z2-9]{8}\Z')
_WORKER = re.compile(r'[A-Za-z0-9_-]{8,96}\Z')


class PairingError(ValueError):
    pass


@dataclass
class Pending:
    code_hash: bytes
    worker_id: str
    expires_at: float
    approved: bool = False
    attempts: int = 0


class PairingRegistry:
    """Single-use codes, explicit approval, expiring bearer tokens, revocation."""

    def __init__(self, *, clock=time.monotonic, code_ttl=300, token_ttl=3600):
        if not 1 <= code_ttl <= 600 or not 1 <= token_ttl <= 86400:
            raise ValueError('Invalid TTL')
        self._clock = clock
        self._code_ttl = code_ttl
        self._token_ttl = token_ttl
        self._secret = secrets.token_bytes(32)
        self._pending: dict[str, Pending] = {}
        self._tokens: dict[bytes, tuple[str, float]] = {}
        self._lock = threading.RLock()

    def _digest(self, value: str) -> bytes:
        return hmac.new(self._secret, value.encode('ascii'), hashlib.sha256).digest()

    def create(self, worker_id: str) -> str:
        if not isinstance(worker_id, str) or not _WORKER.fullmatch(worker_id):
            raise PairingError('Invalid worker identifier')
        with self._lock:
            self._prune()
            if sum(p.worker_id == worker_id for p in self._pending.values()) >= 1:
                raise PairingError('Worker already has a pending pairing')
            if len(self._pending) >= 100:
                raise PairingError('Too many pending pairings')
            alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
            while True:
                code = ''.join(secrets.choice(alphabet) for _ in range(8))
                key = self._digest(code).hex()
                if key not in self._pending:
                    break
            self._pending[key] = Pending(self._digest(code), worker_id, self._clock() + self._code_ttl)
            return code

    def approve(self, code: str) -> None:
        """Only call after server-side admin authentication and authorization."""
        with self._lock:
            pending = self._get(code)
            pending.approved = True

    def redeem(self, code: str, worker_id: str) -> str:
        with self._lock:
            pending = self._get(code)
            if not isinstance(worker_id, str) or not hmac.compare_digest(pending.worker_id, worker_id):
                pending.attempts += 1
                if pending.attempts >= 5:
                    self._pending.pop(self._digest(code).hex(), None)
                raise PairingError('Invalid pairing request')
            if not pending.approved:
                raise PairingError('Pairing not approved')
            self._pending.pop(self._digest(code).hex())
            token = secrets.token_urlsafe(32)
            self._tokens[self._digest(token)] = (worker_id, self._clock() + self._token_ttl)
            return token

    def authorize(self, token: str, worker_id: str) -> bool:
        if not isinstance(token, str) or not isinstance(worker_id, str):
            return False
        with self._lock:
            self._prune()
            record = self._tokens.get(self._digest(token))
            return bool(record and hmac.compare_digest(record[0], worker_id))

    def revoke(self, token: str) -> None:
        """Only call after server-side admin authentication and authorization."""
        if not isinstance(token, str):
            raise PairingError('Invalid token')
        with self._lock:
            self._tokens.pop(self._digest(token), None)

    def _get(self, code: str) -> Pending:
        if not isinstance(code, str) or not _CODE.fullmatch(code):
            raise PairingError('Invalid pairing code')
        self._prune()
        pending = self._pending.get(self._digest(code).hex())
        if pending is None:
            raise PairingError('Pairing code expired or invalid')
        return pending

    def _prune(self) -> None:
        now = self._clock()
        self._pending = {k: v for k, v in self._pending.items() if v.expires_at > now}
        self._tokens = {k: v for k, v in self._tokens.items() if v[1] > now}
