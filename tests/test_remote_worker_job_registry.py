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
        conn.execute("INSERT INTO remote_worker_job_state VALUES (?, NULL, 'idle_confirmed', NULL, ?)", (worker, now[0]))
    registry.record_assignment(worker, job, deadline=200)
    assert WorkerJobRegistry(db, clock=lambda: now[0]).state(worker) == 'running'
    with pytest.raises(ValueError):
        registry.record_assignment(worker, 'job_87654321', deadline=200)
    now[0] = 201
    assert WorkerJobRegistry(db, clock=lambda: now[0]).state(worker) == 'unknown'
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
        db.execute("INSERT INTO remote_worker_job_state VALUES (?, NULL, 'idle_confirmed', NULL, ?)",
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
