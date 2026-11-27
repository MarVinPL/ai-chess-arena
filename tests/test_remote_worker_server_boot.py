"""Execute the actual server startup block against an isolated SQLite file."""
import ast
import signal
from threading import Event
from pathlib import Path
from types import SimpleNamespace

from remote_worker_job_registry import WorkerJobRegistry
from remote_worker_jobs import DurableWorkerJobs


def test_server_startup_invalidates_jobs_before_listener(tmp_path):
    path = tmp_path / 'arena.sqlite3'
    registry = WorkerJobRegistry(path, clock=lambda: 100.0)
    registry.record_assignment('worker_12345678', 'job_12345678', deadline=200.0)
    registry.record_assignment('worker_87654321', 'job_87654321', deadline=200.0)
    registry.record_terminal('worker_87654321', 'job_87654321')
    assert registry.state('worker_87654321') == 'idle_confirmed'
    durable = DurableWorkerJobs(path, clock=lambda: 100)
    durable.assign('worker_99999999', 'job_99999999', timeout_seconds=90)
    assert durable.state('worker_99999999') == 'running'

    source = Path(__file__).resolve().parents[1] / 'server.py'
    tree = ast.parse(source.read_text())
    main = next(node for node in tree.body if isinstance(node, ast.If)
                and isinstance(node.test, ast.Compare)
                and isinstance(node.test.left, ast.Name)
                and node.test.left.id == '__name__')
    boot = compile(ast.Module(body=main.body, type_ignores=[]), str(source), 'exec')
    started = []

    class FakeThread:
        def __init__(self, **kwargs):
            pass
        def start(self):
            started.append('thread')

    class FakeServer:
        def __init__(self, address, handler):
            assert address == ('127.0.0.1', 0)
            assert WorkerJobRegistry(path).state('worker_12345678') == 'unknown'
            assert WorkerJobRegistry(path).state('worker_87654321') == 'unknown'
            assert DurableWorkerJobs(path).state('worker_99999999') == 'unknown'
            self.server_address = address
            started.append('listener')
        def serve_forever(self):
            started.append('serve')

    ns = {
        'DB': path, 'db_init': lambda: started.append('db_init'),
        'remote_worker_job_registry': SimpleNamespace(WorkerJobRegistry=WorkerJobRegistry),
        'remote_worker_jobs': SimpleNamespace(DurableWorkerJobs=DurableWorkerJobs),
        'threading': SimpleNamespace(Thread=FakeThread, Event=Event),
        'signal': signal,
        'resolve_runtime_deployment': lambda: SimpleNamespace(supervisor_requested=False, production_requested=False, supervisor_socket=None),
        'recover_runtime_on_startup': lambda **kw: {'executor': {'count': 0}},
        'runtime_executor': SimpleNamespace(recover_orphaned_jobs=lambda: None),
        'RuntimeSupervisorClientRunner': lambda **kw: None,
        'background_jobs_enabled': lambda: True,
        'LOG_DIR': tmp_path / 'logs',
        '_serve_http_until_shutdown': lambda http, event: http.serve_forever(),
        '_hf_download_planner_loop': lambda: None,
        'hf_candidate_queue': SimpleNamespace(start_background_workers=lambda: started.append('queue')),
        'hf_candidate_runtime': SimpleNamespace(start_background_planner=lambda: started.append('planner')),
        '_prune_jsonl': lambda *args: None,
        'LIVE_EVENTS_LOG': tmp_path / 'events', 'LIVE_EVENT_RETENTION_SECONDS': 100,
        'MODEL_RESPONSES_LOG': tmp_path / 'responses', 'MODEL_RESPONSE_RETENTION_SECONDS': 100,
        'HOST': '127.0.0.1', 'PORT': 0, 'Handler': object,
        'ThreadingHTTPServer': FakeServer,
    }
    try:
        exec(boot, ns)
    finally:
        if '_coordinator_lock' in ns:
            ns['_coordinator_lock'].close()
    assert started == ['db_init', 'thread', 'queue', 'planner', 'listener', 'serve']


def test_server_boot_fails_closed_when_recovery_fails(tmp_path):
    source = Path(__file__).resolve().parents[1] / 'server.py'
    tree = ast.parse(source.read_text())
    main = next(node for node in tree.body if isinstance(node, ast.If)
                and isinstance(node.test, ast.Compare)
                and isinstance(node.test.left, ast.Name)
                and node.test.left.id == '__name__')
    boot = compile(ast.Module(body=main.body, type_ignores=[]), str(source), 'exec')
    class BrokenRegistry:
        def __init__(self, path):
            pass
        def recover_after_restart(self):
            raise OSError('SQLite unavailable')
    started = []
    ns = {'DB': tmp_path / 'broken.sqlite', 'db_init': lambda: started.append('db_init'),
          'remote_worker_job_registry': SimpleNamespace(WorkerJobRegistry=BrokenRegistry),
          'remote_worker_jobs': SimpleNamespace(DurableWorkerJobs=DurableWorkerJobs),
          'threading': SimpleNamespace(Thread=lambda **kwargs: started.append('thread'))}
    import pytest
    try:
        with pytest.raises(OSError, match='SQLite unavailable'):
            exec(boot, ns)
    finally:
        if '_coordinator_lock' in ns:
            ns['_coordinator_lock'].close()
    assert started == ['db_init']
