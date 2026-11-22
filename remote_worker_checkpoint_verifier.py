"""Strict domain validation for a resumable remote chess move checkpoint.

Integrity at rest is checked separately in WorkerUpdatePause. This validator
replays verified UCI moves to reject impossible positions; it is NOT an
authentication or STOP-acknowledgement mechanism.
"""
import re

import chess

_JOB_ID = re.compile(r"[A-Za-z0-9_-]{8,96}\Z")
_UCI = re.compile(r"[a-h][1-8][a-h][1-8][qrbn]?\Z")
_MAX_PLIES = 1024


def verify_chess_checkpoint(checkpoint):
    if not isinstance(checkpoint, dict) or set(checkpoint) != {
        "schema_version", "job_id", "resume_mode", "payload"
    } or type(checkpoint["schema_version"]) is not int or (
        checkpoint["schema_version"] != 1
    ) or not isinstance(checkpoint["job_id"], str) or not (
        _JOB_ID.fullmatch(checkpoint["job_id"])
    ) or checkpoint["resume_mode"] not in ("resume", "restart"):
        return False

    payload = checkpoint["payload"]
    if not isinstance(payload, dict):
        return False

    if checkpoint["resume_mode"] == "restart":
        # Starting fresh must never smuggle an unverified game position.
        return not payload

    if set(payload) != {"initial_fen", "moves_uci", "current_fen"}:
        return False
    if payload["initial_fen"] != chess.STARTING_FEN:
        return False
    moves = payload["moves_uci"]
    if not isinstance(moves, list) or len(moves) > _MAX_PLIES or not (
        isinstance(payload["current_fen"], str) and
        len(payload["current_fen"]) <= 120
    ):
        return False

    board = chess.Board()
    try:
        for raw in moves:
            if not isinstance(raw, str) or not _UCI.fullmatch(raw):
                return False
            move = chess.Move.from_uci(raw)
            if move not in board.legal_moves:
                return False
            board.push(move)
    except (ValueError, AssertionError):
        return False
    return board.fen() == payload["current_fen"]
