"""Issue #192: untrusted remote inference job/result validation (no network listener).

Only chess inference data is exchanged. Never accept commands or local file paths
from the coordinator in the worker protocol.
"""
from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from typing import Any

import chess

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
    if version == 3:
        required.add('lease_token')
    elif version not in (1, 2):
        raise ProtocolError('Unsupported protocol version')
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
        board = chess.Board(data['fen'])
        if not board.is_valid():
            raise ProtocolError('Invalid board state')
    except ValueError as exc:
        raise ProtocolError('Invalid FEN') from exc
    if version == 3 and (not isinstance(data['lease_token'], str)
                         or not _SHA.fullmatch(data['lease_token'])):
        raise ProtocolError('Invalid lease fencing token')
    if version in (2, 3):
        moves = data['legal_moves']
        expected = sorted(move.uci() for move in board.legal_moves)
        if (not isinstance(moves, list) or not 1 <= len(moves) <= 256
                or moves != expected):
            raise ProtocolError('Legal moves differ from Arena board position')
    return dict(data)


def validate_result(message: Any, job: dict, board: chess.Board) -> chess.Move:
    """Verify correlation and legal UCI at the authoritative VPS coordinator."""
    # Version 3 tasks require a fresh per-assignment 256-bit capability on
    # EVERY result. Legacy version-1 results cannot downgrade a fenced task.
    # This is independent of the machine authentication token.
    if job.get('version') == 3:
        data = _mapping(message, {'version', 'job_id', 'uci', 'lease_token'})
        if type(data['version']) is not int or data['version'] != 2:
            raise ProtocolError('Fenced result requires protocol version 2')
        if (not isinstance(data['lease_token'], str)
                or not _SHA.fullmatch(data['lease_token'])
                or not secrets.compare_digest(data['lease_token'], job['lease_token'])):
            raise ProtocolError('Stale or mismatched lease fencing token')
    else:
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
