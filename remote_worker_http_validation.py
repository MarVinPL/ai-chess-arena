"""Strict bounded JSON request parsing shared by Worker HTTP adapters.

This module does not authenticate, authorize, dispatch or mutate job state.
Callers must apply their rate limiter before reading a request, and enforce
machine authentication and exact protocol fields before domain operations.
"""
import json
import math

MAX_BODY = 2048
READ_TIMEOUT = 5
MAX_JSON_DEPTH = 16


class WorkerRequestError(ValueError):
    """Safe HTTP status; never include untrusted request contents."""

    def __init__(self, status):
        super().__init__('Invalid Worker request')
        self.status = status


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key')
        result[key] = value
    return result


def _reject_constant(_):
    raise ValueError('Non-finite JSON number')


def _validate_tree(payload):
    pending = [(payload, 1)]
    while pending:
        value, depth = pending.pop()
        if depth > MAX_JSON_DEPTH:
            raise ValueError('JSON nesting limit exceeded')
        if isinstance(value, dict):
            pending.extend((child, depth + 1) for child in value.values())
        elif isinstance(value, list):
            pending.extend((child, depth + 1) for child in value)
        elif isinstance(value, float) and not math.isfinite(value):
            raise ValueError('Non-finite JSON number')


def read_worker_json(handler, expected_path, *, max_body=MAX_BODY):
    """Read exactly one UTF-8 JSON object, rejecting ambiguous HTTP framing.

    The caller owns response generation. Header/body rejections close the
    connection so unread bytes cannot be parsed as a subsequent request.
    Socket timeout is an inactivity bound, not an absolute request deadline.
    """
    if type(max_body) is not int or not 1 <= max_body <= MAX_BODY:
        raise ValueError('Invalid Worker body limit')
    try:
        if handler.path != expected_path:
            raise WorkerRequestError(400)
        headers = handler.headers
        if headers.get_all('Transfer-Encoding') or headers.get_all('Content-Encoding'):
            raise WorkerRequestError(400)
        lengths = headers.get_all('Content-Length') or []
        if len(lengths) != 1:
            raise WorkerRequestError(400)
        raw_length = lengths[0]
        if not raw_length.isascii() or not raw_length.isdecimal():
            raise WorkerRequestError(400)
        # Avoid parsing arbitrarily large integers from untrusted headers.
        if len(raw_length) > len(str(max_body)):
            raise WorkerRequestError(413)
        length = int(raw_length)
        if not 0 < length <= max_body:
            raise WorkerRequestError(413)
        content_types = headers.get_all('Content-Type') or []
        if len(content_types) != 1 or content_types[0].split(';', 1)[0].strip().lower() != 'application/json':
            raise WorkerRequestError(415)
        previous_timeout = handler.connection.gettimeout()
        handler.connection.settimeout(READ_TIMEOUT)
        try:
            raw = handler.rfile.read(length)
        finally:
            handler.connection.settimeout(previous_timeout)
        if len(raw) != length:
            raise WorkerRequestError(400)
        payload = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique_object,
                             parse_constant=_reject_constant)
        if not isinstance(payload, dict):
            raise WorkerRequestError(400)
        _validate_tree(payload)
        return payload
    except WorkerRequestError:
        handler.close_connection = True
        raise
    except TimeoutError:
        handler.close_connection = True
        raise WorkerRequestError(408) from None
    except (ValueError, UnicodeError, RecursionError, OSError):
        handler.close_connection = True
        raise WorkerRequestError(400) from None
