import pytest
from remote_worker_job_registry import WorkerJobRegistry


def test_unknown_until_coordinator_assigns_and_confirms_terminal(tmp_path):
    now = [100.0]
    db = tmp_path / 'jobs.sqlite'
    registry = WorkerJobRegistry(db, clock=lambda: now[0])
    worker, job = 'worker_12345678', 'job_12345678'
    assert registry.state(worker) == 'unknown'
    with pytest.raises(ValueError):
        registry.record_assignment(worker, job, deadline=99)
    # The coordinator must establish a trustworthy initial idle state before
    # assignment; no API currently performs this transition.
    with registry._connect() as conn:
        conn.execute("INSERT INTO worker_job_safety VALUES (?, NULL, 'idle_confirmed', NULL, ?)", (worker, now[0]))
    registry.record_assignment(worker, job, deadline=200)
    assert WorkerJobRegistry(db, clock=lambda: now[0]).state(worker) == 'running'
    with pytest.raises(ValueError):
        registry.record_assignment(worker, 'job_87654321', deadline=200)
    now[0] = 201
    assert WorkerJobRegistry(db, clock=lambda: now[0]).state(worker) == 'unknown'
    with pytest.raises(ValueError):
        registry.record_terminal(worker, job)
    assert registry.state(worker) == 'unknown'
    # Coordinator must reconcile expired work; an on-time result may finish.
    now[0] = 150
    registry.record_terminal(worker, job)
    assert WorkerJobRegistry(db, clock=lambda: now[0]).state(worker) == 'idle_confirmed'
    with pytest.raises(ValueError):
        registry.record_terminal(worker, job)


def test_invalid_worker_and_no_implicit_idle(tmp_path):
    registry = WorkerJobRegistry(tmp_path / 'jobs.sqlite')
    with pytest.raises(ValueError):
        registry.state('bad')
    assert registry.state('worker_87654321') == 'unknown'


def test_coordinator_restart_invalidates_persisted_states(tmp_path):
    db = tmp_path / 'jobs.sqlite'
    now = [100.0]
    registry = WorkerJobRegistry(db, clock=lambda: now[0])
    registry.record_assignment('worker_12345678', 'job_12345678', deadline=200)
    registry.record_assignment('worker_87654321', 'job_87654321', deadline=200)
    registry.record_terminal('worker_87654321', 'job_87654321')
    assert registry.state('worker_87654321') == 'idle_confirmed'
    WorkerJobRegistry(db, clock=lambda: now[0]).recover_after_restart()
    assert registry.state('worker_12345678') == 'unknown'
    assert registry.state('worker_87654321') == 'unknown'


def test_server_boot_recovers_before_starting_listener():
    from pathlib import Path
    source = (Path(__file__).resolve().parents[1] / 'server.py').read_text()
    boot = source.split("if __name__ == '__main__':", 1)[1]
    assert boot.index('WorkerJobRegistry(DB).recover_after_restart()') < boot.index('ThreadingHTTPServer(')
    assert boot.index('WorkerJobRegistry(DB).recover_after_restart()') < boot.index('_hf_download_planner_loop')


def test_isolated_server_boot_invalidates_real_sqlite_before_listener(tmp_path):
    """Execute actual __main__ startup body with external services stubbed."""
    import ast
    from pathlib import Path
    import server

    path = tmp_path / 'isolated.sqlite'
    registry = WorkerJobRegistry(path)
    worker = 'worker_12345678'
    with registry._connect() as db:
        db.execute("INSERT INTO worker_job_safety VALUES (?, NULL, 'idle_confirmed', NULL, ?)",
                   (worker, 100.0))
    source = (Path(__file__).resolve().parents[1] / 'server.py').read_text()
    module = ast.parse(source)
    entry = next(node for node in module.body if isinstance(node, ast.If)
                 and isinstance(node.test, ast.Compare)
                 and isinstance(node.test.left, ast.Name)
                 and node.test.left.id == '__name__')
    executed = []

    class FakeThread:
        def __init__(self, **kwargs):
            pass
        def start(self):
            executed.append('thread')

    class FakeHTTPServer:
        def __init__(self, *_):
            assert registry.state(worker) == 'unknown'
            executed.append('listener')
        def serve_forever(self):
            pass

    globals_for_boot = dict(vars(server), DB=path,
                            db_init=lambda: executed.append('db_init'),
                            threading=type('Threading', (), {'Thread': FakeThread}),
                            ThreadingHTTPServer=FakeHTTPServer,
                            _prune_jsonl=lambda *_: None,
                            hf_candidate_queue=type('Queue', (), {'start_background_workers': staticmethod(lambda: None)}),
                            hf_candidate_runtime=type('Runtime', (), {'start_background_planner': staticmethod(lambda: None)}))
    exec(compile(ast.Module(body=entry.body, type_ignores=[]), '<isolated-server-boot>', 'exec'),
         globals_for_boot)
    assert executed == ['db_init', 'thread', 'listener']
    assert registry.state(worker) == 'unknown'


def test_api_and_scheduler_share_canonical_registry(tmp_path):
    from remote_worker_jobs import DurableWorkerJobs
    path = tmp_path / 'jobs.sqlite'
    now = lambda: 100
    scheduler = DurableWorkerJobs(path, clock=now)
    api = WorkerJobRegistry(path, clock=now)
    scheduler.assign('worker_12345678', 'job_12345678', timeout_seconds=30)
    assert api.state('worker_12345678') == 'running'
    api.record_terminal('worker_12345678', 'job_12345678')
    assert scheduler.state('worker_12345678') == 'idle_confirmed'
    api.recover_after_restart()
    assert scheduler.state('worker_12345678') == 'unknown'


def test_legacy_idle_state_is_never_trusted(tmp_path):
    import sqlite3
    path = tmp_path / 'jobs.sqlite'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE remote_worker_job_state (worker_id TEXT PRIMARY KEY, job_id TEXT, state TEXT, deadline REAL, updated_at REAL)')
        db.execute("INSERT INTO remote_worker_job_state VALUES ('worker_12345678', NULL, 'idle_confirmed', NULL, 100)")
    api = WorkerJobRegistry(path, clock=lambda: 100)
    assert api.state('worker_12345678') == 'unknown'
    with pytest.raises(ValueError):
        api.record_terminal('worker_12345678', 'job_12345678')


def test_cross_api_concurrent_assignments_are_serialized(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from remote_worker_jobs import DurableWorkerJobs
    path = tmp_path / 'jobs.sqlite'
    worker = 'worker_12345678'
    jobs = DurableWorkerJobs(path, clock=lambda: 100)
    jobs.assign(worker, 'job_00000000', timeout_seconds=90)
    jobs.complete(worker, 'job_00000000')

    def assign(i):
        try:
            if i % 2:
                WorkerJobRegistry(path, clock=lambda: 100).record_assignment(
                    worker, f'job_{i:08d}', deadline=190)
            else:
                DurableWorkerJobs(path, clock=lambda: 100).assign(
                    worker, f'job_{i:08d}', timeout_seconds=90)
            return True
        except ValueError:
            return False

    with ThreadPoolExecutor(max_workers=12) as pool:
        assert list(pool.map(assign, range(12))).count(True) == 1
    assert jobs.state(worker) == 'running'


def test_recovery_prevents_late_completion(tmp_path):
    from remote_worker_jobs import DurableWorkerJobs
    path = tmp_path / 'jobs.sqlite'
    jobs = DurableWorkerJobs(path, clock=lambda: 100)
    jobs.assign('worker_12345678', 'job_12345678', timeout_seconds=90)
    WorkerJobRegistry(path, clock=lambda: 100).recover_after_restart()
    with pytest.raises(ValueError):
        jobs.complete('worker_12345678', 'job_12345678')
    assert WorkerJobRegistry(path, clock=lambda: 100).state('worker_12345678') == 'unknown'


def test_api_and_scheduler_concurrent_assignments_are_serialized(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from remote_worker_jobs import DurableWorkerJobs
    path = tmp_path / 'jobs.sqlite'
    worker = 'worker_12345678'
    clock = lambda: 100
    initial = DurableWorkerJobs(path, clock=clock)
    initial.assign(worker, 'job_00000000', timeout_seconds=30)
    initial.complete(worker, 'job_00000000')

    def attempt(i):
        try:
            if i % 2:
                WorkerJobRegistry(path, clock=clock).record_assignment(
                    worker, f'job_{i:08d}', deadline=130)
            else:
                DurableWorkerJobs(path, clock=clock).assign(
                    worker, f'job_{i:08d}', timeout_seconds=30)
            return True
        except ValueError:
            return False

    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(attempt, range(1, 13)))
    assert sum(results) == 1
    assert WorkerJobRegistry(path, clock=clock).state(worker) == 'running'


def test_restart_rejects_late_result_from_previous_coordinator(tmp_path):
    from remote_worker_jobs import DurableWorkerJobs
    path = tmp_path / 'jobs.sqlite'
    jobs = DurableWorkerJobs(path, clock=lambda: 100)
    jobs.assign('worker_12345678', 'job_12345678', timeout_seconds=30)
    WorkerJobRegistry(path, clock=lambda: 110).recover_after_restart()
    with pytest.raises(ValueError):
        jobs.complete('worker_12345678', 'job_12345678')
    assert WorkerJobRegistry(path, clock=lambda: 110).state('worker_12345678') == 'unknown'
