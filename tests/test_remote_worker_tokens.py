import sqlite3
import pytest
from remote_worker_tokens import WorkerTokenStore, WorkerTokenError

WORKER = 'worker_12345678'


def test_issue_persists_and_revokes(tmp_path):
    now = [1000]
    path = tmp_path / 'workers.db'
    store = WorkerTokenStore(path, clock=lambda: now[0])
    token = store.issue(WORKER, ttl_seconds=60)
    assert store.authenticate(WORKER, token)
    assert WorkerTokenStore(path, clock=lambda: now[0]).authenticate(WORKER, token)
    assert not store.authenticate(WORKER, 'wrong')
    assert not store.authenticate('worker_87654321', token)
    with sqlite3.connect(path) as db:
        assert token not in repr(db.execute('SELECT * FROM worker_tokens').fetchall())
    store.revoke(WORKER)
    assert not store.authenticate(WORKER, token)


def test_rotation_and_expiry(tmp_path):
    now = [1000]
    store = WorkerTokenStore(tmp_path / 'tokens.db', clock=lambda: now[0])
    first = store.issue(WORKER, ttl_seconds=60)
    second = store.issue(WORKER, ttl_seconds=60)
    assert first != second
    assert not store.authenticate(WORKER, first)
    assert store.authenticate(WORKER, second)
    now[0] += 60
    assert not store.authenticate(WORKER, second)


def test_invalid_input(tmp_path):
    store = WorkerTokenStore(tmp_path / 'tokens.db')
    with pytest.raises(WorkerTokenError):
        store.issue('../invalid')
    with pytest.raises(WorkerTokenError):
        store.issue(WORKER, ttl_seconds=True)
    assert not store.authenticate(WORKER, None)
