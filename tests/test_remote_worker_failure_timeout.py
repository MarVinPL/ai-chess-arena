"""Timed-out coordinator waits cannot silently free or duplicate a remote job."""
import threading
import time
import pytest

from remote_worker_coordinator_transport import RemoteResultTimeout, StagedResultTransport
from remote_worker_heartbeat import WorkerHeartbeatStore
from remote_worker_job_binding import BoundWorkerResults
from remote_worker_recovery_report import inspect_unresolved_jobs
from remote_worker_tokens import WorkerTokenStore
from test_remote_worker_job_binding import setup_game


def test_timeout_and_cancel_preserve_original_reservation(tmp_path):
    path, _, checkpoint, digest = setup_game(tmp_path)
    jobs = BoundWorkerResults(path)
    worker = 'worker_12345678'
    token = WorkerTokenStore(path).issue(worker, ttl_seconds=600)
    with jobs._db() as db:
        db.execute("INSERT INTO worker_job_safety VALUES (?,NULL,'idle_confirmed',NULL,0)", (worker,))
    WorkerHeartbeatStore(path).record(worker, token)
    jobs.assign_bound(worker, 'job_12345678', tournament_id=1, pairing_id=7,
                      expected_sha256=digest, timeout_seconds=600,
                      require_fresh_heartbeat=True)
    transport = StagedResultTransport(path, worker, poll_interval=0.01)
    job = {'job_id': 'job_12345678'}
    with pytest.raises(RemoteResultTimeout):
        transport.wait(job, None, None, timeout=0.02)
    stop = threading.Event()
    stop.set()
    with pytest.raises(InterruptedError):
        transport.wait(job, None, None, stop_event=stop, timeout=0.02)
    with pytest.raises(ValueError):
        jobs.assign_bound(worker, 'job_87654321', tournament_id=1, pairing_id=7,
                          expected_sha256=digest, timeout_seconds=600,
                          require_fresh_heartbeat=True)
    assert jobs.state(worker) == 'running'
    assert inspect_unresolved_jobs(path)[0]['job_id'] == 'job_12345678'
    with jobs._db() as db:
        assert db.execute('SELECT count(*) FROM remote_job_binding').fetchone()[0] == 1
