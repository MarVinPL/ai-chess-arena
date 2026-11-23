"""Heartbeat is necessary but not sufficient to dispatch a remote job."""
import sqlite3
import time
import pytest
from remote_worker_heartbeat import WorkerHeartbeatStore
from remote_worker_readiness import assert_worker_heartbeat_ready, WorkerNotReady
from remote_worker_tokens import WorkerTokenStore
from test_remote_worker_job_binding import setup_game


def test_ready_requires_fresh_heartbeat_and_idle(tmp_path):
    path, jobs, _, digest = setup_game(tmp_path)
    worker = 'worker_12345678'
    token = WorkerTokenStore(path).issue(worker, ttl_seconds=600)
    with jobs._db() as db:
        db.execute("INSERT INTO worker_job_safety VALUES (?,NULL,'idle_confirmed',NULL,0)", (worker,))
    with pytest.raises(WorkerNotReady, match='heartbeat'):
        assert_worker_heartbeat_ready(path, worker)
    heartbeats = WorkerHeartbeatStore(path)
    heartbeats.record(worker, token)
    assert assert_worker_heartbeat_ready(path, worker)
    with sqlite3.connect(path) as db:
        db.execute('UPDATE worker_heartbeats SET observed_at=? WHERE worker_id=?',
                   (time.time() - 90, worker))
    with pytest.raises(WorkerNotReady, match='heartbeat'):
        assert_worker_heartbeat_ready(path, worker)
    heartbeats.record(worker, token)
    jobs.assign_bound(worker, 'job_12345678', tournament_id=1, pairing_id=7,
                      expected_sha256=digest, timeout_seconds=90)
    with pytest.raises(WorkerNotReady, match='idle'):
        assert_worker_heartbeat_ready(path, worker)


def test_heartbeat_cannot_recover_unknown_or_revoked_worker(tmp_path):
    path, jobs, _, _ = setup_game(tmp_path)
    worker = 'worker_12345678'
    tokens = WorkerTokenStore(path)
    token = tokens.issue(worker, ttl_seconds=600)
    heartbeats = WorkerHeartbeatStore(path)
    heartbeats.record(worker, token)
    with pytest.raises(WorkerNotReady, match='idle'):
        assert_worker_heartbeat_ready(path, worker)
    with jobs._db() as db:
        db.execute("INSERT INTO worker_job_safety VALUES (?,NULL,'idle_confirmed',NULL,0)", (worker,))
    tokens.revoke(worker)
    with pytest.raises(WorkerNotReady, match='credential'):
        assert_worker_heartbeat_ready(path, worker)
