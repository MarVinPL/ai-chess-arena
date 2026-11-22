"""In-memory, transport-independent pairing and job lifecycle for issue #192.

Not a persistent auth service: restart invalidates every pending pairing and token.
The caller must provide TLS, authorization of approve(), rate limiting and
persistent audit logging before exposing this component to a network.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass
from typing import Callable
from remote_worker_metrics import WorkerMetrics

from remote_worker_protocol import ProtocolError, validate_job, validate_result
import chess


@dataclass
class _Pairing:
    code_hash: bytes
    expires: float
    approved: bool = False


@dataclass
class _Session:
    token_hash: bytes
    expires: float
    revoked: bool = False


@dataclass
class _Job:
    payload: dict
    worker_id: str
    expires: float
    committed: bool = False
    assigned_at: float = 0.0


class WorkerSessions:
    def __init__(self, clock: Callable[[], float] = time.monotonic, metrics: WorkerMetrics | None = None):
        self._clock = clock
        self._metrics = metrics
        self._pairings: dict[str, _Pairing] = {}
        self._sessions: dict[str, _Session] = {}
        self._jobs: dict[str, _Job] = {}

    @staticmethod
    def _digest(secret: str) -> bytes:
        return hashlib.sha256(secret.encode('utf-8')).digest()

    def request_pairing(self, worker_id: str) -> str:
        if not isinstance(worker_id, str) or not 8 <= len(worker_id) <= 96 or not worker_id.isascii() or not all(c.isalnum() or c in '_-' for c in worker_id):
            raise ProtocolError('Invalid worker ID')
        if worker_id in self._sessions and not self._sessions[worker_id].revoked:
            raise ProtocolError('Worker already paired')
        code = secrets.token_urlsafe(24)
        self._pairings[worker_id] = _Pairing(self._digest(code), self._clock() + 300)
        return code

    def approve(self, worker_id: str, code: str) -> str:
        """Must be called only after independent admin authentication/approval."""
        p = self._pairings.get(worker_id)
        if not p or p.approved or self._clock() >= p.expires or not isinstance(code, str) or not hmac.compare_digest(p.code_hash, self._digest(code)):
            raise ProtocolError('Pairing denied')
        p.approved = True
        del self._pairings[worker_id]
        token = secrets.token_urlsafe(32)
        self._sessions[worker_id] = _Session(self._digest(token), self._clock() + 3600)
        return token

    def authenticate(self, worker_id: str, token: str) -> None:
        s = self._sessions.get(worker_id)
        if not s or s.revoked or self._clock() >= s.expires or not isinstance(token, str) or not hmac.compare_digest(s.token_hash, self._digest(token)):
            raise ProtocolError('Worker authentication failed')

    def update_safety_state(self, worker_id: str, token: str) -> str:
        """Coordinator-side advisory state; never an install authorization.

        A live authenticated session is required. Timed-out jobs are UNKNOWN,
        not idle, until the coordinator explicitly resolves them.
        """
        self.authenticate(worker_id, token)
        jobs = [job for job in self._jobs.values() if job.worker_id == worker_id and not job.committed]
        if any(self._clock() >= job.expires for job in jobs):
            return 'unknown'
        return 'running' if jobs else 'idle_confirmed'

    def revoke(self, worker_id: str) -> None:
        s = self._sessions.get(worker_id)
        if s:
            s.revoked = True
        for job_id, job in list(self._jobs.items()):
            if job.worker_id == worker_id:
                del self._jobs[job_id]

    def assign(self, worker_id: str, token: str, job: dict) -> None:
        self.authenticate(worker_id, token)
        payload = validate_job(job)
        if payload['job_id'] in self._jobs:
            raise ProtocolError('Duplicate job ID')
        self._jobs[payload['job_id']] = _Job(payload, worker_id, self._clock() + payload['timeout_seconds'], assigned_at=self._clock())

    def accept(self, worker_id: str, token: str, result: dict, board: chess.Board) -> chess.Move:
        self.authenticate(worker_id, token)
        if not isinstance(result, dict) or not isinstance(result.get('job_id'), str):
            raise ProtocolError('Invalid result')
        job = self._jobs.get(result['job_id'])
        if not job or job.worker_id != worker_id or job.committed or self._clock() >= job.expires:
            raise ProtocolError('Stale, duplicate or foreign job result')
        if board.fen() != job.payload['fen']:
            raise ProtocolError('Board changed since assignment')
        move = validate_result(result, job.payload, board)
        # Persist the coordinator-validated result before marking it committed.
        # This is wall time, not a claimed worker CPU/GPU utilization metric.
        if self._metrics is not None:
            self._metrics.record(job_id=result['job_id'], worker_id=worker_id,
                                 status='success', duration_ms=max(0, int((self._clock() - job.assigned_at) * 1000)),
                                 moves=1)
        job.committed = True
        return move

    def terminate(self, worker_id: str, token: str, job_id: str, *, status: str) -> None:
        """Coordinator-confirmed failure/cancellation; never trust raw worker status."""
        self.authenticate(worker_id, token)
        if status not in ('error', 'cancelled'):
            raise ProtocolError('Invalid terminal status')
        job = self._jobs.get(job_id)
        if not job or job.worker_id != worker_id or job.committed:
            raise ProtocolError('Unknown or completed job')
        if self._metrics is not None:
            self._metrics.record(job_id=job_id, worker_id=worker_id,
                                 status=status, duration_ms=max(0, int((self._clock() - job.assigned_at) * 1000)))
        job.committed = True
