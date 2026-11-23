import sqlite3
from pathlib import Path

import remote_worker_tokens

def test_token_issue_and_revoke(tmp_path):
    db = tmp_path / 'worker.sqlite'
    store = remote_worker_tokens.WorkerTokenStore(db)
    token = store.issue('android_marvin_01', ttl_seconds=3600)
    assert store.authenticate('android_marvin_01', token)
    assert not store.authenticate('android_marvin_01', 'bad')
    assert not store.authenticate('other_worker_01', token)
    with sqlite3.connect(db) as con:
        assert token not in repr(con.execute('SELECT * FROM worker_tokens').fetchall())
    store.revoke('android_marvin_01')
    assert not store.authenticate('android_marvin_01', token)

def test_worker_auth_endpoint_is_owned_by_machine_backend():
    root = Path(__file__).resolve().parents[1]
    source = (root / 'server.py').read_text()
    worker = (root / 'worker_backend.py').read_text()
    assert "worker_backend.dispatch_worker_post(" in source
    assert 'if path == AUTH_ROUTE:' in worker
    assert 'WorkerTokenStore(db).authenticate(worker_id, token)' in worker
    # The browser session is never an alternative to a Worker machine token.
    assert 'authenticated_user' not in worker
