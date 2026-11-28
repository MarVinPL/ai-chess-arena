"""Issue #192: untrusted remote inference job/result validation (no network listener).

Only chess inference data is exchanged. Never accept commands or local file paths
from the coordinator in the worker protocol.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import chess
from elimination_chess import board_for_variant

_ID = re.compile(r"[A-Za-z0-9_-]{8,96}\Z")
_SHA = re.compile(r"[a-f0-9]{64}\Z")
_UCI = re.compile(r"(?:[a-h][1-8]){2}[qrbn]?\Z")
MAX_MESSAGE_BYTES = 16384


class ProtocolError(ValueError):
    """Reject a malformed or unauthorized remote worker message."""


def _mapping(message: Any, keys: set[str]) -> dict:
    if not isinstance(message, dict) or set(message) != keys:
        raise ProtocolError('Unexpected or missing protocol fields')
    if len(repr(message).encode('utf-8')) > MAX_MESSAGE_BYTES:
        raise ProtocolError('Message exceeds size limit')
    return message


def validate_job(message: Any) -> dict:
    """Validate a strictly typed inference task before dispatch to a worker."""
    if not isinstance(message, dict) or type(message.get('version')) is not int:
        raise ProtocolError('Unsupported protocol version')
    version = message['version']
    required = {'version', 'job_id', 'fen', 'model_sha256',
                'max_tokens', 'timeout_seconds'}
    if version in (2, 3):
        required.add('legal_moves')
    elif version != 1:
        raise ProtocolError('Unsupported protocol version')
    if version == 3:
        required.add('variant')
    data = _mapping(message, required)
    if not isinstance(data['job_id'], str) or not _ID.fullmatch(data['job_id']):
        raise ProtocolError('Invalid job ID')
    if not isinstance(data['model_sha256'], str) or not _SHA.fullmatch(data['model_sha256']):
        raise ProtocolError('Invalid model digest')
    if type(data['max_tokens']) is not int or not 1 <= data['max_tokens'] <= 1024:
        raise ProtocolError('Invalid token budget')
    if type(data['timeout_seconds']) is not int or not 1 <= data['timeout_seconds'] <= 600:
        raise ProtocolError('Invalid time budget')
    if not isinstance(data['fen'], str) or len(data['fen']) > 120:
        raise ProtocolError('Invalid FEN')
    try:
        board = board_for_variant(data['fen'], data.get('variant', 'standard'))
    except ValueError as exc:
        raise ProtocolError('Invalid FEN') from exc
    if version in (2, 3):
        moves = data['legal_moves']
        expected = sorted(move.uci() for move in board.legal_moves)
        if (not isinstance(moves, list) or not 1 <= len(moves) <= 256
                or moves != expected):
            raise ProtocolError('Legal moves differ from Arena board position')
    return dict(data)


def validate_result(message: Any, job: dict, board: chess.Board) -> chess.Move:
    """Verify correlation and legal UCI at the authoritative VPS coordinator."""
    if 'fen' in job and board.fen() != job['fen']:
        raise ProtocolError('Board changed since assignment')
    if 'variant' in job or job.get('version') in (1, 2, 3) and 'model_sha256' in job:
        expected_variant = job.get('variant', 'standard')
        actual_variant = 'elimination' if board.uci_variant == 'chess-elimination' else 'standard'
        if expected_variant != actual_variant:
            raise ProtocolError('Board variant differs from assignment')
    data = _mapping(message, {'version', 'job_id', 'uci'})
    if type(data['version']) is not int or data['version'] != 1:
        raise ProtocolError('Unsupported protocol version')
    if data['job_id'] != job['job_id']:
        raise ProtocolError('Unexpected job ID')
    if not isinstance(data['uci'], str) or not _UCI.fullmatch(data['uci']):
        raise ProtocolError('Invalid UCI format')
    try:
        move = chess.Move.from_uci(data['uci'])
    except ValueError as exc:
        raise ProtocolError('Invalid UCI') from exc
    if move not in board.legal_moves:
        raise ProtocolError('Illegal move')
    return move
