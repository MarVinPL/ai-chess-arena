"""Cancellation regression tests: isolated fake children and HTTP requests."""
import threading
import subprocess
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch
import pytest
from engine_runner import LlamafileRuntime, RunnerError
from upstream_llama_runtime import UpstreamLlamaServerRuntime
from runtime_upgrade_plan import UpstreamLlamaServerProfile
import passkey_test

class Child:
    def __init__(self):
        self.dead = threading.Event()
        self.terms = 0
    def poll(self):
        return -15 if self.dead.is_set() else None
    def terminate(self):
        self.terms += 1
        self.dead.set()
    def wait(self, timeout=None):
        if not self.dead.wait(timeout):
            raise subprocess.TimeoutExpired("fake", timeout)
        return -15
    kill = terminate

@pytest.fixture(params=["llamafile", "upstream"])
def runtime_case(request, tmp_path):
    binary = tmp_path / "engine"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o700)
    model = tmp_path / "test.gguf"
    model.write_bytes(b"fixture")
    child = Child()
    popen = Mock(return_value=child)
    if request.param == "upstream":
        runtime = UpstreamLlamaServerRuntime(
            profile=UpstreamLlamaServerProfile(binary=binary),
            runtime_root=tmp_path, model_root=tmp_path, popen=popen,
            monitor_interval=3600)
    else:
        runtime = LlamafileRuntime(binary=binary, popen=popen, monitor_interval=3600)
    try:
        yield runtime, model, child, popen
    finally:
        runtime.shutdown()

def test_cancel_while_loading(runtime_case):
    runtime, model, child, popen = runtime_case
    entered = threading.Event()
    def unhealthy():
        entered.set()
        return False
    with patch.object(runtime, "_healthy", side_effect=unhealthy):
        with ThreadPoolExecutor(max_workers=2) as pool:
            loading = pool.submit(runtime.ensure_loaded, model)
            assert entered.wait(2)
            pool.submit(runtime.request_cancel).result(timeout=4)
            with pytest.raises(RunnerError, match="zatrzymany"):
                loading.result(timeout=4)
        runtime.request_cancel()
        runtime._monitor_once()
        with pytest.raises(RunnerError, match="zatrzymany"):
            runtime.ensure_loaded(model)
    assert child.dead.is_set()
    assert child.terms == 1
    assert popen.call_count == 1

def test_cancel_blocking_passkey(runtime_case):
    runtime, model, child, popen = runtime_case
    entered = threading.Event()
    stop = threading.Event()
    def blocked_open(req, timeout):
        assert timeout is None
        assert req.full_url == runtime.api_base + "/v1/chat/completions"
        entered.set()
        assert child.dead.wait(3)
        raise ConnectionResetError("engine exited")
    with patch.object(runtime, "_healthy", return_value=True), patch.object(
        passkey_test.urllib.request, "urlopen", side_effect=blocked_open):
        with ThreadPoolExecutor(max_workers=2) as pool:
            answer = pool.submit(passkey_test.ask_passkey_question, runtime, model, "2+2?", stop)
            assert entered.wait(2)
            stop.set()
            pool.submit(runtime.request_cancel).result(timeout=4)
            assert answer.result(timeout=4)["status"] == "stopped"
    runtime._monitor_once()
    assert popen.call_count == 1

def test_transport_error_without_stop_is_not_success(runtime_case):
    runtime, model, child, _ = runtime_case
    with patch.object(runtime, "_healthy", return_value=True), patch.object(
        passkey_test.urllib.request, "urlopen", side_effect=ConnectionResetError):
        with pytest.raises(ConnectionResetError):
            passkey_test.ask_passkey_question(runtime, model, "2+2?", threading.Event())

def test_failed_termination_keeps_child_reference(runtime_case):
    runtime, model, child, _ = runtime_case
    with patch.object(runtime, "_healthy", return_value=True):
        runtime.ensure_loaded(model)
    with patch.object(child, "terminate", side_effect=OSError("failure")):
        with pytest.raises(OSError):
            runtime.request_cancel()
        assert runtime._process is child
        assert not child.dead.is_set()
    runtime.request_cancel()
    assert child.dead.is_set()

def test_tournament_stop_and_resume_preserves_answers(tmp_path, monkeypatch):
    import sqlite3
    import server
    import league
    db = tmp_path / "arena.sqlite3"
    monkeypatch.setattr(server, "DB", db)
    monkeypatch.setattr(league, "DB", db)
    monkeypatch.setattr(server, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(server, "MODEL_RESPONSES_LOG", tmp_path / "responses.jsonl")
    monkeypatch.setattr(server, "LIVE_EVENTS_LOG", tmp_path / "events.jsonl")
    monkeypatch.setattr(server, "tournament_thread", None)
    monkeypatch.setattr(server, "tournament_runtime", None)
    monkeypatch.setattr(server, "tournament_stop_event", threading.Event())
    monkeypatch.setattr(server, "tournament_error", "")
    monkeypatch.setattr(server, "set_live", lambda *a, **kw: None)
    monkeypatch.setattr(server, "cleanup_unneeded_models", lambda *a: None)
    monkeypatch.setattr(server, "tournament_preflight", lambda *a: {
        "ready": True, "entrants": [1, 2], "runtime_available": True})
    monkeypatch.setattr(server, "_prepare_single_model", lambda *a, **kw: {
        "path": str(tmp_path / "fake.gguf")})
    server.db_init()
    for name, digest in (("Stop A", "a"*64), ("Stop B", "b"*64)):
        league.register_model(name, source="https://example.test/model.gguf",
            license="Apache-2.0", license_verified=True,
            metadata={"filename": name.replace(" ", "-")+".gguf", "sha256": digest, "size_bytes": 100})
    tid = league.create_round_robin("STOP integration")["id"]
    runtimes = []
    entered = threading.Event()
    calls = []
    class Runtime:
        def __init__(self):
            self.cancelled = threading.Event()
            self.closed = False
        def ensure_loaded(self, *args):
            assert not self.cancelled.is_set()
        def request_cancel(self):
            self.cancelled.set()
        def stop(self):
            pass
        def shutdown(self):
            self.closed = True
    def create(**kw):
        runtime = Runtime()
        runtimes.append(runtime)
        return runtime
    def answer(runtime, path, question, stop_event, **kw):
        calls.append(question)
        if len(calls) == 1:
            return {"status": "answered", "answer": "fixture", "seconds": 0.01}
        entered.set()
        assert runtime.cancelled.wait(3)
        return {"status": "stopped", "answer": "", "seconds": None}
    monkeypatch.setattr(server.runtime_factory, "create_runtime", create)
    monkeypatch.setattr(server.passkey_test, "ask_passkey_question", answer)
    def stop_run():
        entered.clear()
        server.start_tournament(tid)
        assert entered.wait(3), server.tournament_error
        server.tournament_stop_event.set()
        server.tournament_thread.join(4)
        assert not server.tournament_thread.is_alive()
        assert runtimes[-1].closed
        with sqlite3.connect(db) as c:
            assert c.execute("select status from tournaments where id=?", (tid,)).fetchone()[0] == "stopped", server.tournament_error
            assert c.execute("select count(*) from passkey_test_answers where tournament_id=?", (tid,)).fetchone()[0] == 1
            assert c.execute("select count(*) from games where tournament_id=?", (tid,)).fetchone()[0] == 0
    try:
        stop_run()
        stop_run()
        assert len(runtimes) == 2
        assert calls[1] == calls[2]
        assert calls[0] != calls[2]
    finally:
        server.tournament_stop_event.set()
        if server.tournament_thread:
            server.tournament_thread.join(4)

@pytest.mark.parametrize("late_stop", [False, True])
def test_tournament_stop_and_resume_preserves_completed_answers(tmp_path, monkeypatch, late_stop):
    import sqlite3
    import server
    import league
    db = tmp_path / "arena.sqlite3"
    monkeypatch.setattr(server, "DB", db)
    monkeypatch.setattr(league, "DB", db)
    monkeypatch.setattr(server, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(server, "MODEL_RESPONSES_LOG", tmp_path / "responses.jsonl")
    monkeypatch.setattr(server, "LIVE_EVENTS_LOG", tmp_path / "events.jsonl")
    monkeypatch.setattr(server, "set_live", lambda *a, **k: None)
    monkeypatch.setattr(server, "tournament_runtime", None)
    monkeypatch.setattr(server, "tournament_thread", None)
    monkeypatch.setattr(server, "tournament_error", "")
    monkeypatch.setattr(server, "tournament_stop_event", threading.Event())
    monkeypatch.setattr(server, "cleanup_unneeded_models", lambda *a, **k: None)
    monkeypatch.setattr(server, "_prepare_single_model", lambda *a, **k: {"path": str(tmp_path / "fixture.gguf")})
    monkeypatch.setattr(server, "tournament_preflight", lambda *a: {"ready": True, "entrants": [1, 2], "runtime_available": True})
    server.db_init()
    for name, digest in (("Alpha", "a"*64), ("Beta", "b"*64)):
        league.register_model(name, source=f"https://example.test/{name}.gguf",
            license="Apache-2.0", license_verified=True,
            metadata={"filename": name+".gguf", "sha256": digest, "size_bytes": 100})
    tid = league.create_round_robin("STOP test")["id"]
    entered = threading.Event()
    instances = []
    closing = threading.Event()
    release = threading.Event()
    class Runtime:
        def __init__(self):
            self.cancelled = threading.Event()
            self.closed = False
            instances.append(self)
        def ensure_loaded(self, *a): pass
        def request_cancel(self): self.cancelled.set()
        def stop(self): pass
        def shutdown(self):
            if late_stop:
                closing.set()
                assert release.wait(3)
            self.closed = True
    monkeypatch.setattr(server.runtime_factory, "create_runtime", lambda **kw: Runtime())
    calls = []
    def ask(runtime, path, question, event, **kwargs):
        calls.append(question)
        if len(calls) == 1:
            return {"status": "answered", "answer": "four", "seconds": 0.1}
        entered.set()
        if not late_stop:
            assert runtime.cancelled.wait(3)
        return {"status": "stopped", "answer": "", "seconds": None}
    monkeypatch.setattr(passkey_test, "ask_passkey_question", ask)
    if late_stop:
        # Force exit from pairing selection with unfinished pairings.
        monkeypatch.setattr(server, "_select_tournament_pairing", lambda *a: (None, None))
    if late_stop:
        server.start_tournament(tid)
        try:
            assert closing.wait(3), server.tournament_error
            with sqlite3.connect(db) as c:
                assert c.execute("select status from tournaments where id=?", (tid,)).fetchone()[0] == "running"
            server.tournament_stop_event.set()
        finally:
            release.set()
            server.tournament_thread.join(5)
        assert not server.tournament_thread.is_alive()
        assert instances[0].closed
        with sqlite3.connect(db) as c:
            assert c.execute("select status from tournaments where id=?", (tid,)).fetchone()[0] == "stopped"
        return
    for expected_calls in (2, 3):
        entered.clear()
        server.start_tournament(tid)
        try:
            assert entered.wait(3)
            server.tournament_stop_event.set()
            server.tournament_thread.join(5)
            assert not server.tournament_thread.is_alive()
            assert instances[-1].closed
            with sqlite3.connect(db) as c:
                assert c.execute("select status from tournaments where id=?", (tid,)).fetchone()[0] == "stopped"
                assert c.execute("select count(*) from passkey_test_answers where tournament_id=? and status='answered'", (tid,)).fetchone()[0] == 1
                assert c.execute("select count(*) from games where tournament_id=?", (tid,)).fetchone()[0] == 0
            assert len(calls) == expected_calls
        finally:
            server.tournament_stop_event.set()
            server.tournament_thread.join(5)
    assert len(instances) == 2
    assert calls[1] == calls[2]
