"""Arena-owned move provider for on-phone inference.

The phone generates only a UCI proposal. This provider awaits a coordinator-
validated result while engine_runner and tournament_checkpoint remain the
single authority for board state, move history and checkpoint persistence.

The production tournament does not select this provider automatically.
"""
from __future__ import annotations

import secrets
import time
from typing import Callable

import chess

from remote_worker_inference_delivery import WorkerInferenceDelivery


class PhoneMoveUnavailable(RuntimeError):
    """An assigned phone move could not be safely completed."""


class ArenaPhoneMoveProvider:
    def __init__(
        self, database, worker_id, *, clock=time.time, sleep=time.sleep,
        poll_seconds=0.25, timeout_seconds=600,
        on_assignment: Callable | None = None,
        on_heartbeat: Callable | None = None,
    ):
        if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 600:
            raise ValueError("Invalid phone inference timeout")
        if not 0 < poll_seconds <= 2:
            raise ValueError("Invalid phone polling interval")
        self.delivery = WorkerInferenceDelivery(database, clock=clock)
        self.delivery.jobs._id(worker_id)
        self.worker_id = worker_id
        self.clock = clock
        self.sleep = sleep
        self.poll_seconds = poll_seconds
        self.timeout_seconds = timeout_seconds
        self.on_assignment = on_assignment
        self.on_heartbeat = on_heartbeat

    def __call__(self, board: chess.Board, player: dict, stop_event, kwargs: dict):
        if not isinstance(board, chess.Board) or not board.is_valid():
            raise PhoneMoveUnavailable("Invalid Arena board for phone inference")
        if not isinstance(player, dict) or not isinstance(kwargs, dict):
            raise PhoneMoveUnavailable("Missing trusted player configuration")
        digest = player.get("model_sha256")
        tokens = kwargs.get("max_tokens", 16)
        if type(tokens) is not int or not 1 <= tokens <= 1024:
            raise PhoneMoveUnavailable("Unsupported model token budget")
        job_id = "move_" + secrets.token_hex(16)
        job = {
            "version": 2,
            "job_id": job_id,
            "fen": board.fen(),
            "legal_moves": sorted(move.uci() for move in board.legal_moves),
            "model_sha256": digest,
            "max_tokens": tokens,
            "timeout_seconds": self.timeout_seconds,
        }
        # No remote dispatch until the selected phone has a FRESH verified
        # ready marker matching this model and no update/pause/resume barriers.
        try:
            self.delivery.assign_from_arena(self.worker_id, job)
        except ValueError as exc:
            raise PhoneMoveUnavailable("Phone not ready for this Arena model") from exc

        if self.on_assignment is not None:
            # Owner-provided callback publishes Arena state. It must not
            # allocate a second board or checkpoint writer on the phone.
            self.on_assignment(self.worker_id, job_id, board.fen(), player)
        started = self.clock()
        next_heartbeat = started + 10
        while True:
            if stop_event is not None and stop_event.is_set():
                # The assignment remains fenced until explicit coordinator
                # reconciliation; never mark a stopped job idle.
                raise PhoneMoveUnavailable("Arena paused the remote inference")
            if self.clock() - started >= self.timeout_seconds:
                raise PhoneMoveUnavailable("Remote inference expired")
            try:
                uci = self.delivery.read_arena_result(self.worker_id, job_id)
            except ValueError as exc:
                raise PhoneMoveUnavailable("Remote result lease invalid") from exc
            if uci is not None:
                # This operation validates the exact assigned FEN, commits
                # acceptance in Arena, then the owning runner pushes/checkpoints.
                move = self.delivery.finalize_arena_result(
                    self.worker_id, job_id, uci,
                )
                if move not in board.legal_moves:
                    raise PhoneMoveUnavailable("Arena rejected illegal phone UCI")
                return move, {
                    "seconds": max(0.0, min(3600.0, self.clock() - started)),
                    "runtime": "android-local-llama-cpp",
                }
            # Refresh the Arena-owned read-only live snapshot during slow
            # inference. Never transfer board/checkpoint ownership to phone.
            if self.on_heartbeat is not None and self.clock() >= next_heartbeat:
                self.on_heartbeat(self.worker_id, job_id, board.fen(), player)
                next_heartbeat = self.clock() + 10
            self.sleep(self.poll_seconds)


class ArenaPhonePoolProvider:
    """Pick a ready phone per model and never fall back after remote dispatch.

    Explicit opt-in only. Arena remains the one tournament checkpoint writer.
    """

    def __init__(self, database, *, on_assignment=None, clock=time.time,
                 sleep=time.sleep, timeout_seconds=600):
        self.database = database
        self.delivery = WorkerInferenceDelivery(database, clock=clock)
        self.on_assignment = on_assignment
        self.on_heartbeat = on_assignment
        self.clock = clock
        self.sleep = sleep
        self.timeout_seconds = timeout_seconds

    def __call__(self, board: chess.Board, player: dict, stop_event, kwargs: dict):
        if not isinstance(player, dict):
            raise PhoneMoveUnavailable("Invalid player for phone selection")
        sha256 = player.get("model_sha256")
        try:
            worker_id = self.delivery.find_ready_worker(sha256)
        except ValueError as exc:
            raise PhoneMoveUnavailable("Player model has no verified SHA-256") from exc
        if worker_id is None:
            raise PhoneMoveUnavailable(
                "No ready phone with the required model; remote execution stays blocked"
            )
        provider = ArenaPhoneMoveProvider(
            self.database, worker_id, clock=self.clock, sleep=self.sleep,
            timeout_seconds=self.timeout_seconds,
            on_assignment=self.on_assignment,
            on_heartbeat=self.on_heartbeat,
        )
        return provider(board, player, stop_event, kwargs)
