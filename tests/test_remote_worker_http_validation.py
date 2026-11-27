import io
from email.message import Message
from types import SimpleNamespace

import pytest

from remote_worker_http_validation import READ_TIMEOUT, WorkerRequestError, read_worker_json

PATH = '/api/remote-workers/authenticate'


class Connection:
    def __init__(self):
        self.timeout = 17
        self.history = []

    def gettimeout(self):
        return self.timeout

    def settimeout(self, value):
        self.history.append(value)
        self.timeout = value


def handler(raw=b'{}', *, headers=None, path=PATH):
    message = Message()
    for name, value in (headers if headers is not None else [('Content-Length', str(len(raw))), ('Content-Type', 'application/json')]):
        message[name] = value
    return SimpleNamespace(path=path, headers=message, rfile=io.BytesIO(raw),
                           connection=Connection(), close_connection=False)


def test_exact_utf8_object_and_timeout_restoration():
    h = handler('{"label":"żółć"}'.encode())
    assert read_worker_json(h, PATH) == {'label': 'żółć'}
    assert h.connection.history == [READ_TIMEOUT, 17]
    assert not h.close_connection


@pytest.mark.parametrize('headers,status', [
    ([], 400),
    ([('Content-Length', '2'), ('Content-Length', '2'), ('Content-Type', 'application/json')], 400),
    ([('Content-Length', '2, 2'), ('Content-Type', 'application/json')], 400),
    ([('Content-Length', '+2'), ('Content-Type', 'application/json')], 400),
    ([('Content-Length', '２'), ('Content-Type', 'application/json')], 400),
    ([('Content-Length', '0'), ('Content-Type', 'application/json')], 413),
    ([('Content-Length', '2049'), ('Content-Type', 'application/json')], 413),
    ([('Content-Length', '9' * 5000), ('Content-Type', 'application/json')], 413),
    ([('Content-Length', '2'), ('Content-Type', 'text/plain')], 415),
    ([('Content-Length', '2')], 415),
    ([('Content-Length', '2'), ('Content-Type', 'application/json'), ('Content-Type', 'application/json')], 415),
    ([('Content-Length', '2'), ('Content-Type', 'application/json'), ('Transfer-Encoding', '')], 400),
    ([('Content-Length', '2'), ('Content-Type', 'application/json'), ('Transfer-Encoding', 'chunked')], 400),
    ([('Content-Length', '2'), ('Content-Type', 'application/json'), ('Content-Encoding', 'gzip')], 400),
])
def test_bad_headers_rejected_before_body_read(headers, status):
    h = handler(headers=headers)
    with pytest.raises(WorkerRequestError) as exc:
        read_worker_json(h, PATH)
    assert exc.value.status == status
    assert h.close_connection and h.rfile.tell() == 0


@pytest.mark.parametrize('raw', [b'[]', b'null', b'"secret"', b'{', b'{"a":1,"a":2}',
    b'{"nested":{"a":1,"a":2}}', b'{"n":NaN}', b'{"n":1e999}', b'{"n":Infinity}', b'{"n":-Infinity}',
    b'{"a":"\xff"}', b'{}{}', b'{"n":'+b'['*1000+b'0'+b']'*1000+b'}'])
def test_invalid_json_rejected_without_echo(raw):
    h = handler(raw)
    with pytest.raises(WorkerRequestError) as exc:
        read_worker_json(h, PATH)
    assert exc.value.status == 400
    assert str(exc.value) == 'Invalid Worker request'
    assert h.close_connection and h.connection.timeout == 17


def test_exact_path_and_short_body():
    for h in [handler(path=PATH+'?extra=1'), handler(headers=[('Content-Length','4'),('Content-Type','application/json')])]:
        with pytest.raises(WorkerRequestError) as exc:
            read_worker_json(h, PATH)
        assert exc.value.status == 400 and h.close_connection


def test_body_timeout_closes_connection_and_restores_timeout():
    h = handler()
    class Stalled:
        def read(self, _):
            raise TimeoutError('private socket detail')
    h.rfile = Stalled()
    with pytest.raises(WorkerRequestError) as exc:
        read_worker_json(h, PATH)
    assert exc.value.status == 408 and h.close_connection
    assert h.connection.timeout == 17


def test_exact_size_limit():
    raw = b'{"a":"' + b'x' * 2040 + b'"}'
    assert len(raw) == 2048
    assert len(read_worker_json(handler(raw), PATH)['a']) == 2040
