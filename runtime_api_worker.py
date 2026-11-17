"""Opt-in API-side dispatcher for TEST_ONLY runtime jobs.

The worker is inert unless the caller supplies a validated production profile
and the independent execution environment switch is enabled. It only selects
job IDs; the executor revalidates every gate before handing the job to the
supervisor client runner.
"""
from __future__ import annotations

import os
import threading
from typing import Callable

import league
import runtime_executor


class RuntimeApiWorker:
    def __init__(self, *, deployment, runner: Callable,
                 interval_seconds: float = 1.0,
                 execute: Callable = runtime_executor.execute_job,
                 find_next: Callable | None = None):
        if not getattr(deployment, "production_requested", False):
            raise ValueError("Runtime API worker requires the production deployment profile.")
        if not getattr(deployment, "execution_enabled", False):
            raise ValueError("Runtime API worker requires execution-enabled deployment configuration.")
        if not callable(runner) or not callable(execute):
            raise TypeError("Runtime runner and executor must be callable.")
        if isinstance(interval_seconds, bool) or not isinstance(interval_seconds, (int, float)):
            raise ValueError("Worker interval must be numeric.")
        if not 0.1 <= interval_seconds <= 30:
            raise ValueError("Worker interval must be between 0.1 and 30 seconds.")
        self._deployment = deployment
        self._runner = runner
        self._execute = execute
        self._find_next = find_next or self._find_next_queued_job
        self._interval = float(interval_seconds)
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_error: str | None = None
        self.last_result: dict | None = None

    @staticmethod
    def _find_next_queued_job() -> int | None:
        if os.environ.get("ARENA_RUNTIME_EXECUTION_ENABLED") != "1":
            return None
        with league.connect() as connection:
            runtime_executor._init(connection)
            runtime_executor._expire_batches(connection)
            settings = connection.execute(
                "SELECT enabled FROM runtime_execution_settings WHERE id=1"
            ).fetchone()
            if not settings or not settings["enabled"]:
                return None
            row = connection.execute(
                "SELECT j.id FROM runtime_test_jobs j "
                "JOIN runtime_test_batches b ON b.id=j.batch_id "
                "JOIN model_permission_requests p ON p.id=j.permission_request_id "
                "WHERE j.status='queued' AND b.status IN ('approved','running') "
                "AND (b.status='running' OR b.expires_at>?) "
                "AND p.status='approved' AND p.scope='TEST_ONLY' "
                "ORDER BY j.id LIMIT 1",
                (runtime_executor._now(),),
            ).fetchone()
        return int(row["id"]) if row else None

    def run_once(self) -> dict:
        if os.environ.get("ARENA_RUNTIME_EXECUTION_ENABLED") != "1":
            self.last_result = {"status": "inert", "job_id": None}
            return dict(self.last_result)
        job_id = self._find_next()
        if job_id is None:
            self.last_result = {"status": "idle", "job_id": None}
            return dict(self.last_result)
        if isinstance(job_id, bool) or not isinstance(job_id, int) or job_id < 1:
            raise RuntimeError("Queue selector returned an invalid job ID.")
        try:
            result = self._execute(job_id, runner=self._runner)
        except Exception as exc:
            self.last_error = type(exc).__name__
            self.last_result = {"status": "error", "job_id": job_id,
                                "error_type": self.last_error}
            return dict(self.last_result)
        self.last_error = None
        self.last_result = {"status": "dispatched", "job_id": job_id,
                            "result_status": result.get("status") if isinstance(result, dict) else None}
        return dict(self.last_result)

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.run_once()
            except Exception as exc:
                self.last_error = type(exc).__name__
                self.last_result = {"status": "error", "error_type": self.last_error}
            if self._stop_event.wait(self._interval):
                break

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        if os.environ.get("ARENA_RUNTIME_EXECUTION_ENABLED") != "1":
            raise RuntimeError("Runtime API worker remains inert without independent execution opt-in.")
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, name="arena-runtime-api-dispatcher", daemon=True
        )
        self._thread.start()

    def stop(self, *, timeout_seconds: float = 5.0) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout_seconds)
            if thread.is_alive():
                raise RuntimeError("Runtime API worker did not stop cleanly.")
            self._thread = None
