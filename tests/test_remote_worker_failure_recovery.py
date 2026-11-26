"""Offline failure matrix: lost worker, stale heartbeat, coordinator restart."""
import sqlite3
import time
import pytest

from remote_worker_heartbeat import WorkerHeartbeatStore
from remote_worker_job_binding import BoundWorkerResults
from remote_worker_jobs import DurableWorkerJobs
from remote_worker_readiness import WorkerNotReady, assert_worker_heartbeat_ready
from remote_worker_recovery_report import inspect_unresolved_jobs
from remote_worker_tokens import WorkerTokenStore
from test_remote_worker_job_binding import setup_game


def _prepare(tmp_path):
    path, _, old, digest = setup_game(tmp_path)
    jobs = BoundWorkerResults(path)
    worker = 'worker_12345678'
    token = WorkerTokenStore(path).issue(worker, ttl_seconds=600)
    with jobs._db() as db:
        db.execute("INSERT INTO worker_job_safety VALUES (?,NULL,'idle_confirmed',NULL,0)", (worker,))
    heartbeats = WorkerHeartbeatStore(path)
    heartbeats.record(worker, token)
    return path, jobs, worker, digest, heartbeats


def _reserve(jobs, worker, digest, job='job_12345678'):
    return jobs.assign_bound(worker, job, tournament_id=1, pairing_id=7,
                             expected_sha256=digest, timeout_seconds=600,
                             require_fresh_heartbeat=True)


def test_lost_connection_retains_inflight_job_and_checkpoint(tmp_path):
    path, jobs, worker, digest, heartbeats = _prepare(tmp_path)
    _reserve(jobs, worker, digest)
    with sqlite3.connect(path) as db:
        db.execute('UPDATE worker_heartbeats SET observed_at=? WHERE worker_id=?',
                   (time.time()-90, worker))
    with pytest.raises(WorkerNotReady):
        assert_worker_heartbeat_ready(path, worker)
    with pytest.raises(ValueError):
        _reserve(jobs, worker, digest, 'job_87654321')
    assert inspect_unresolved_jobs(path)[0]['action'] == 'wait_for_worker'
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT count(*) FROM remote_job_binding').fetchone()[0] == 1
        assert db.execute('SELECT count(*) FROM tournament_checkpoints').fetchone()[0] == 1


def test_restart_and_reconnect_do_not_reassign_unknown_job(tmp_path):
    path, jobs, worker, digest, heartbeats = _prepare(tmp_path)
    _reserve(jobs, worker, digest)
    DurableWorkerJobs(path).recover_after_restart()
    heartbeats.record(worker, WorkerTokenStore(path).issue(worker, ttl_seconds=600))
    assert jobs.state(worker) == 'unknown'
    with pytest.raises(WorkerNotReady):
        assert_worker_heartbeat_ready(path, worker)
    with pytest.raises(ValueError):
        _reserve(jobs, worker, digest, 'job_87654321')
    report = inspect_unresolved_jobs(path)
    assert len(report) == 1
    assert report[0]['action'] == 'manual_reconciliation'
    assert report[0]['checkpoint_matches']


def test_stale_heartbeat_blocks_new_job_even_when_idle(tmp_path):
    path, jobs, worker, digest, heartbeats = _prepare(tmp_path)
    with sqlite3.connect(path) as db:
        db.execute('UPDATE worker_heartbeats SET observed_at=? WHERE worker_id=?',
                   (time.time()-90, worker))
    with pytest.raises(ValueError, match='heartbeat'):
        _reserve(jobs, worker, digest)
    assert jobs.state(worker) == 'idle_confirmed'
    assert inspect_unresolved_jobs(path) == []
