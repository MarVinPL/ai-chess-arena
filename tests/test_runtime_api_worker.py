import os
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import league
import model_permissions
import runtime_executor
from runtime_api_worker import RuntimeApiWorker


class Production:
    production_requested = True
    execution_enabled = True


class Disabled:
    production_requested = False
    execution_enabled = False


def test_worker_requires_validated_production_profile():
    with pytest.raises(ValueError, match="production deployment"):
        RuntimeApiWorker(deployment=Disabled(), runner=lambda *_: {})


def test_worker_remains_inert_without_independent_environment_opt_in():
    worker = RuntimeApiWorker(
        deployment=Production(), runner=lambda *_: {},
        find_next=lambda: pytest.fail("queue must not be inspected"),
        execute=lambda *_args, **_kwargs: pytest.fail("job must not execute"),
    )
    with patch.dict(os.environ, {"ARENA_RUNTIME_EXECUTION_ENABLED": "0"}):
        assert worker.run_once() == {"status": "inert", "job_id": None}
        with pytest.raises(RuntimeError, match="independent execution opt-in"):
            worker.start()


def test_worker_dispatches_only_selected_job_id():
    calls = []
    worker = RuntimeApiWorker(
        deployment=Production(), runner=lambda *_: {}, find_next=lambda: 42,
        execute=lambda job_id, *, runner: calls.append((job_id, runner)) or {
            "job_id": job_id, "status": "completed",
        },
    )
    runner = worker._runner
    with patch.dict(os.environ, {"ARENA_RUNTIME_EXECUTION_ENABLED": "1"}):
        result = worker.run_once()
    assert result == {"status": "dispatched", "job_id": 42, "result_status": "completed"}
    assert calls == [(42, runner)]


def test_worker_reports_execution_failure_without_leaking_exception_text():
    def fail(*_args, **_kwargs):
        raise RuntimeError("sensitive implementation detail")
    worker = RuntimeApiWorker(
        deployment=Production(), runner=lambda *_: {}, find_next=lambda: 7,
        execute=fail,
    )
    with patch.dict(os.environ, {"ARENA_RUNTIME_EXECUTION_ENABLED": "1"}):
        assert worker.run_once() == {
            "status": "error", "job_id": 7, "error_type": "RuntimeError",
        }
    assert "sensitive" not in str(worker.last_result)


def test_worker_rejects_invalid_job_id():
    worker = RuntimeApiWorker(
        deployment=Production(), runner=lambda *_: {}, find_next=lambda: True,
        execute=lambda *_args, **_kwargs: pytest.fail("invalid job ID must not execute"),
    )
    with patch.dict(os.environ, {"ARENA_RUNTIME_EXECUTION_ENABLED": "1"}):
        with pytest.raises(RuntimeError, match="invalid job ID"):
            worker.run_once()


def test_worker_stop_joins_dispatch_thread():
    import threading
    import time
    called = threading.Event()
    worker = RuntimeApiWorker(
        deployment=Production(), runner=lambda *_: {}, interval_seconds=0.1,
        find_next=lambda: called.set() or None,
    )
    with patch.dict(os.environ, {"ARENA_RUNTIME_EXECUTION_ENABLED": "1"}):
        worker.start()
        assert called.wait(2)
        worker.stop()
    assert worker._thread is None


def test_queue_selector_filters_to_approved_unexpired_test_only_jobs(tmp_path):
    db = tmp_path / "arena.sqlite3"
    with patch.object(league, "DB", db), patch.dict(
        os.environ, {"ARENA_RUNTIME_EXECUTION_ENABLED": "1"}
    ):
        with league.connect() as connection:
            model_permissions.ensure_schema(connection)
            runtime_executor._init(connection)
            permission = model_permissions.request_permission(
                connection, model_id=1, repo_id="org/model", revision="a" * 40,
                artifact_sha256="b" * 64, scope="TEST_ONLY", confirm=True,
                reason="runtime api worker test", actor="test-operator",
            )
            model_permissions.decide_permission(
                connection, request_id=permission["id"], action="approve",
                confirm=True, actor="test-operator",
            )
            connection.execute(
                "UPDATE runtime_execution_settings SET enabled=1 WHERE id=1"
            )
            cursor = connection.execute(
                "INSERT INTO runtime_test_batches(status,created_by,created_at,approved_by,approved_at,expires_at) "
                "VALUES('approved','test','2026-11-18T00:00:00Z','test','2026-11-18T00:00:00Z','2999-01-01T00:00:00Z')"
            )
            batch_id = cursor.lastrowid
            connection.execute(
                "INSERT INTO runtime_test_jobs(model_id,repo_id,revision,artifact_key,artifact_sha256,permission_request_id,status,timeout_seconds,queued_at,batch_id) "
                "VALUES(1,'org/model',?,'hf:org/model|rev|model.gguf',? ,?,'queued',60,'2026-11-18T00:00:00Z',?)",
                ("a" * 40, "b" * 64, permission["id"], batch_id),
            )
        worker = RuntimeApiWorker(
            deployment=Production(), runner=lambda *_: {},
            execute=lambda *_args, **_kwargs: {},
        )
        assert worker._find_next_queued_job() == 1
        with league.connect() as connection:
            connection.execute("UPDATE model_permission_requests SET status='revoked' WHERE id=?", (permission["id"],))
        assert worker._find_next_queued_job() is None


def test_worker_shutdown_persists_active_job_cancellation_before_join(monkeypatch):
    import threading
    entered = threading.Event()
    cancelled = threading.Event()
    calls = []

    def execute(job_id, *, runner):
        entered.set()
        assert cancelled.wait(2)
        return {"status": "cancelled"}

    worker = RuntimeApiWorker(
        deployment=Production(), runner=lambda *_: {},
        interval_seconds=0.1, find_next=lambda: 91,
        execute=execute,
        cancel_active=lambda job_id: (calls.append(job_id), cancelled.set()),
    )
    monkeypatch.setenv("ARENA_RUNTIME_EXECUTION_ENABLED", "1")
    worker.start()
    assert entered.wait(2)
    worker.stop(timeout_seconds=2)
    assert calls == [91]
    assert worker._thread is None
    assert worker._active_job_id is None


def test_worker_shutdown_does_not_cancel_a_job_selected_after_stop(monkeypatch):
    import threading
    selected = threading.Event()
    release = threading.Event()
    calls = []
    def find_next():
        selected.set()
        release.wait(2)
        return 92
    worker = RuntimeApiWorker(
        deployment=Production(), runner=lambda *_: {},
        interval_seconds=0.1, find_next=find_next,
        execute=lambda *_args, **_kwargs: pytest.fail("job must not execute after shutdown"),
        cancel_active=calls.append,
    )
    monkeypatch.setenv("ARENA_RUNTIME_EXECUTION_ENABLED", "1")
    worker.start()
    assert selected.wait(2)
    # Stop wins before the queue selector hands the selected ID to execution.
    stopped = threading.Event()
    errors = []
    def stop_worker():
        try:
            worker.stop(timeout_seconds=2)
        except Exception as exc:
            errors.append(exc)
        finally:
            stopped.set()
    stopper = threading.Thread(target=stop_worker)
    stopper.start()
    release.set()
    assert stopped.wait(2)
    stopper.join(2)
    assert errors == []
    assert calls == []
