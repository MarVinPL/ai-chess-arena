import pytest
from remote_worker_jobs import DurableWorkerJobs


def test_durable_lifecycle_and_fail_closed(tmp_path):
    path = tmp_path / 'jobs.db'
    now = [100]
    jobs = DurableWorkerJobs(path, clock=lambda: now[0])
    worker, job = 'worker_12345678', 'job_12345678'
    assert jobs.state(worker) == 'unknown'
    jobs.assign(worker, job, timeout_seconds=30)
    assert DurableWorkerJobs(path, clock=lambda: now[0]).state(worker) == 'running'
    with pytest.raises(ValueError):
        jobs.assign(worker, 'job_87654321', timeout_seconds=30)
    now[0] = 131
    assert jobs.state(worker) == 'unknown'
    with pytest.raises(ValueError):
        jobs.complete(worker, job)
    jobs.invalidate(worker)
    assert jobs.state(worker) == 'unknown'


def test_committed_terminal_state_persists(tmp_path):
    path = tmp_path / 'jobs.db'
    jobs = DurableWorkerJobs(path, clock=lambda: 100)
    jobs.assign('worker_12345678', 'job_12345678', timeout_seconds=30)
    jobs.complete('worker_12345678', 'job_12345678')
    assert DurableWorkerJobs(path, clock=lambda: 110).state('worker_12345678') == 'idle_confirmed'
    with pytest.raises(ValueError):
        jobs.complete('worker_12345678', 'job_12345678')
    jobs.invalidate('worker_12345678')
    assert jobs.state('worker_12345678') == 'unknown'


def test_session_coordinator_updates_durable_state(tmp_path):
    import chess
    from remote_worker_sessions import WorkerSessions
    path = tmp_path / 'jobs.db'
    now = [100]
    durable = DurableWorkerJobs(path, clock=lambda: now[0])
    sessions = WorkerSessions(clock=lambda: now[0], durable_jobs=durable)
    worker = 'worker_12345678'
    token = sessions.approve(worker, sessions.request_pairing(worker))
    board = chess.Board()
    job = dict(version=1, job_id='job_12345678', fen=board.fen(),
               model_sha256='b' * 64, max_tokens=16, timeout_seconds=90)
    sessions.assign(worker, token, job)
    assert DurableWorkerJobs(path, clock=lambda: now[0]).state(worker) == 'running'
    sessions.accept(worker, token, dict(version=1, job_id=job['job_id'], uci='e2e4'), board)
    assert DurableWorkerJobs(path, clock=lambda: now[0]).state(worker) == 'idle_confirmed'
    sessions.revoke(worker)
    assert durable.state(worker) == 'unknown'


def test_concurrent_assignment_allows_only_one(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    path = tmp_path / 'jobs.db'
    worker = 'worker_12345678'
    first = DurableWorkerJobs(path, clock=lambda: 100)
    # Explicitly reconcile the worker before allowing another assignment.
    first.assign(worker, 'job_12345678', timeout_seconds=90)
    first.complete(worker, 'job_12345678')

    def attempt(i):
        try:
            DurableWorkerJobs(path, clock=lambda: 100).assign(
                worker, f'job_{i:08d}', timeout_seconds=90)
            return True
        except ValueError:
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(attempt, range(8)))
    assert outcomes.count(True) == 1
    assert DurableWorkerJobs(path, clock=lambda: 100).state(worker) == 'running'


def test_restart_invalidates_persisted_idle_and_running(tmp_path):
    path = tmp_path / 'jobs.db'
    jobs = DurableWorkerJobs(path, clock=lambda: 100)
    jobs.assign('worker_12345678', 'job_12345678', timeout_seconds=90)
    jobs.assign('worker_87654321', 'job_87654321', timeout_seconds=90)
    jobs.complete('worker_87654321', 'job_87654321')
    restarted = DurableWorkerJobs(path, clock=lambda: 110)
    assert restarted.state('worker_87654321') == 'idle_confirmed'
    restarted.recover_after_restart()
    assert restarted.state('worker_12345678') == 'unknown'
    assert restarted.state('worker_87654321') == 'unknown'
    with pytest.raises(ValueError):
        restarted.assign('worker_87654321', 'job_99999999', timeout_seconds=90)


def test_durable_completion_failure_does_not_commit_memory_or_metrics(tmp_path):
    import chess
    from remote_worker_sessions import WorkerSessions
    from remote_worker_metrics import WorkerMetrics
    path = tmp_path / 'jobs.db'
    durable = DurableWorkerJobs(path, clock=lambda: 100)
    metrics = WorkerMetrics(tmp_path / 'metrics.db')
    sessions = WorkerSessions(clock=lambda: 100, durable_jobs=durable, metrics=metrics)
    worker = 'worker_12345678'
    token = sessions.approve(worker, sessions.request_pairing(worker))
    board = chess.Board()
    job = dict(version=1, job_id='job_12345678', fen=board.fen(),
               model_sha256='b' * 64, max_tokens=16, timeout_seconds=90)
    sessions.assign(worker, token, job)
    # Simulate coordinator crash/restart invalidating the active lease.
    durable.recover_after_restart()
    with pytest.raises(ValueError):
        sessions.accept(worker, token, dict(version=1, job_id=job['job_id'], uci='e2e4'), board)
    assert sessions._jobs[job['job_id']].committed is False
    assert durable.state(worker) == 'unknown'
    assert metrics.summary() == []


def test_durable_termination_failure_does_not_commit_memory(tmp_path):
    import chess
    from remote_worker_sessions import WorkerSessions
    durable = DurableWorkerJobs(tmp_path / 'jobs.db', clock=lambda: 100)
    sessions = WorkerSessions(clock=lambda: 100, durable_jobs=durable)
    worker = 'worker_12345678'
    token = sessions.approve(worker, sessions.request_pairing(worker))
    job = dict(version=1, job_id='job_12345678', fen=chess.Board().fen(),
               model_sha256='b' * 64, max_tokens=16, timeout_seconds=90)
    sessions.assign(worker, token, job)
    durable.recover_after_restart()
    with pytest.raises(ValueError):
        sessions.terminate(worker, token, job['job_id'], status='error')
    assert sessions._jobs[job['job_id']].committed is False
    assert durable.state(worker) == 'unknown'


def test_legacy_completion_cannot_bypass_phone_checkpoint(tmp_path):
    import chess
    from remote_worker_inference_delivery import WorkerInferenceDelivery
    path = tmp_path / 'jobs.db'
    worker, job_id = 'worker_12345678', 'job_12345678'
    delivery = WorkerInferenceDelivery(path, clock=lambda: 100)
    delivery.register_local_ready(worker, 'b' * 64, 4)
    board = chess.Board()
    delivery.assign_from_arena(worker, dict(
        version=4, variant='standard', job_id=job_id, fen=board.fen(),
        legal_moves=sorted(m.uci() for m in board.legal_moves),
        model_sha256='b' * 64, max_tokens=16, timeout_seconds=90))
    with pytest.raises(ValueError, match='atomic checkpoint'):
        delivery.jobs.complete(worker, job_id)
    assert delivery.jobs.state(worker) == 'running'
    assert delivery.poll_assigned_job(worker)['state'] == 'assigned'


@pytest.mark.parametrize('barrier', ['hold', 'pause', 'resume'])
def test_legacy_completion_rechecks_barriers_atomically(tmp_path, barrier):
    jobs = DurableWorkerJobs(tmp_path / 'jobs.db', clock=lambda: 100)
    worker, job = 'worker_12345678', 'job_12345678'
    jobs.assign(worker, job, timeout_seconds=90)
    with jobs._db() as db:
        if barrier == 'hold':
            db.execute("INSERT INTO worker_update_holds VALUES (?,?,?,?)",
                       (worker, 'a'*64, 100, 200))
        elif barrier == 'pause':
            db.execute("INSERT INTO worker_update_pause_requests (worker_id,job_id,request_id,phase,created_at,acknowledged_at) VALUES (?,?,?,?,?,?)",
                       (worker, job, 'a'*32, 'requested', 100, None))
        else:
            db.execute("INSERT INTO worker_update_resume VALUES (?,?,?,?,?,?)",
                       (worker, job, 'a'*32, '{}', 'b'*64, 100))
    with pytest.raises(ValueError):
        jobs.complete(worker, job)
    assert jobs.state(worker) == 'running'
