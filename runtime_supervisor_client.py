"""API-side runner that controls only job IDs through the supervisor IPC."""

from __future__ import annotations

import json
import queue
import re
import threading
import time
from pathlib import Path
from typing import Callable

from runtime_supervisor_ipc import supervisor_request
from runtime_unix_bridge import unix_request

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_UNIT = re.compile(r"^ai-chess-arena-runtime-([1-9][0-9]*)\.service$")
_HEALTH_PATH = "/health"
_INFERENCE_PATH = "/v1/chat/completions"


class RuntimeSupervisorClientError(RuntimeError):
    """Raised when the API cannot safely complete a supervised smoke test."""


class RuntimeSupervisorClientRunner:
    """Use supervisor IPC for lifecycle and the Unix bridge for model I/O.

    The runner never receives or supplies an executable, artifact path, unit
    name, systemd property, or environment. It sends only the queue job ID.
    """

    def __init__(self, *, supervisor_socket: str | Path = "/run/arena-runtime/supervisor.sock",
                 bridge_socket_template: str = "/run/arena-runtime/job-{job_id}/control.sock",
                 supervisor_call: Callable = supervisor_request,
                 bridge_call: Callable = unix_request,
                 monotonic: Callable = time.monotonic,
                 sleep: Callable = time.sleep,
                 thread_factory: Callable = threading.Thread):
        self._supervisor_socket = str(supervisor_socket)
        self._bridge_socket_template = bridge_socket_template
        self._supervisor_call = supervisor_call
        self._bridge_call = bridge_call
        self._monotonic = monotonic
        self._sleep = sleep
        self._thread_factory = thread_factory

    def _status(self, job_id: int) -> dict:
        result = self._supervisor_call(
            self._supervisor_socket, "status", job_id, timeout_seconds=5.0
        )
        if not isinstance(result, dict) or result.get("job_id") != job_id:
            raise RuntimeSupervisorClientError("Supervisor returned an invalid lease status.")
        return result

    def _stop_exact(self, job_id: int, expected_unit: str) -> None:
        match = _UNIT.fullmatch(expected_unit)
        if not match or int(match.group(1)) != job_id:
            raise RuntimeSupervisorClientError("Supervisor unit identity does not match the job.")
        try:
            result = self._supervisor_call(
                self._supervisor_socket, "stop", job_id, timeout_seconds=120.0
            )
            if (not isinstance(result, dict) or result.get("job_id") != job_id
                    or result.get("unit_name") != expected_unit
                    or result.get("lease_state") != "stopped"):
                raise RuntimeSupervisorClientError("Supervisor did not confirm exact-unit stop.")
        except Exception as stop_error:
            # The response can be lost after the unit was safely stopped.
            try:
                status = self._status(job_id)
            except Exception as status_error:
                raise RuntimeSupervisorClientError(
                    "Exact-unit stop status is unavailable; durable supervisor recovery is required."
                ) from status_error
            if (status.get("lease_state") == "stopped"
                    and status.get("unit_name") == expected_unit):
                return
            raise RuntimeSupervisorClientError(
                "Exact-unit stop is unconfirmed; durable supervisor recovery is required."
            ) from stop_error

    def _stop_if_leased(self, job_id: int) -> None:
        status = self._status(job_id)
        if not status.get("lease_exists"):
            return
        state = status.get("lease_state")
        if state == "stopped":
            return
        unit_name = status.get("unit_name")
        self._stop_exact(job_id, unit_name)

    def __call__(self, payload: dict, timeout_seconds: int,
                 cancellation_requested: Callable[[], bool]) -> dict:
        if not isinstance(payload, dict):
            raise TypeError("Runtime payload must be an object.")
        job_id = payload.get("id")
        if type(job_id) is not int or job_id < 1:
            raise ValueError("Runtime payload has no valid queue job ID.")
        if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 1800:
            raise ValueError("Runtime timeout is outside policy.")
        if not callable(cancellation_requested):
            raise TypeError("Cancellation callback is required.")
        expected_sha = payload.get("sha256")
        if not isinstance(expected_sha, str) or not _SHA256.fullmatch(expected_sha):
            raise ValueError("Pinned artifact SHA-256 is invalid.")

        try:
            # Artifact handoff and supervisor authorization happen before the
            # systemd unit starts; they must not consume the model runtime budget.
            start_result = self._supervisor_call(
                self._supervisor_socket, "start", job_id, timeout_seconds=1800.0
            )
        except Exception as start_error:
            try:
                self._stop_if_leased(job_id)
            except Exception as recovery_error:
                raise RuntimeSupervisorClientError(
                    "Start outcome is ambiguous; the supervisor must recover the durable lease."
                ) from recovery_error
            raise

        expected_unit = f"ai-chess-arena-runtime-{job_id}.service"
        if (not isinstance(start_result, dict) or start_result.get("job_id") != job_id
                or start_result.get("unit_name") != expected_unit
                or start_result.get("lease_state") != "active"
                or start_result.get("unit_state") != "start_requested"):
            try:
                self._stop_if_leased(job_id)
            except Exception as cleanup_error:
                raise RuntimeSupervisorClientError(
                    "Invalid start response and exact-unit stop is unconfirmed."
                ) from cleanup_error
            raise RuntimeSupervisorClientError("Supervisor did not confirm the expected start request.")

        # The runtime deadline starts only after the supervisor confirms the
        # systemd start request. The unit itself enforces the same wall-clock cap.
        started = self._monotonic()
        deadline = started + timeout_seconds
        bridge_socket = self._bridge_socket_template.format(job_id=job_id)
        ready_at = None
        try:
            while self._monotonic() < deadline:
                if cancellation_requested():
                    raise InterruptedError("Runtime smoke test was cancelled or permission revoked.")
                remaining = deadline - self._monotonic()
                try:
                    status, _body = self._bridge_call(
                        bridge_socket, "GET", _HEALTH_PATH, b"",
                        timeout_seconds=min(0.5, max(0.05, remaining)),
                    )
                    if status == 200:
                        ready_at = self._monotonic()
                        break
                except (OSError, TimeoutError, RuntimeError):
                    pass
                self._sleep(0.2)
            if ready_at is None:
                raise TimeoutError("Supervised model did not become healthy before the deadline.")

            body = json.dumps({
                "messages": [{"role": "user", "content": "Reply with exactly READY."}],
                "temperature": 0, "max_tokens": 8, "stream": False,
            }, separators=(",", ":")).encode("utf-8")
            inference_result: queue.Queue = queue.Queue(maxsize=1)
            remaining = max(0.05, deadline - self._monotonic())

            def infer():
                try:
                    value = self._bridge_call(
                        bridge_socket, "POST", _INFERENCE_PATH, body,
                        timeout_seconds=remaining,
                    )
                    inference_result.put((True, value))
                except Exception as exc:
                    inference_result.put((False, exc))

            thread = self._thread_factory(
                target=infer, name="arena-supervised-smoke-inference", daemon=True
            )
            inference_started = self._monotonic()
            thread.start()
            while thread.is_alive():
                if cancellation_requested():
                    raise InterruptedError("Runtime smoke inference was cancelled or permission revoked.")
                if self._monotonic() >= deadline:
                    raise TimeoutError("Supervised smoke inference exceeded its hard deadline.")
                self._sleep(0.1)

            ok, value = inference_result.get_nowait()
            if not ok:
                raise RuntimeSupervisorClientError("Supervised smoke inference request failed.") from value
            status, response_body = value
            if status != 200:
                raise RuntimeSupervisorClientError(f"Supervised inference returned HTTP {status}.")
            try:
                response = json.loads(response_body.decode("utf-8"))
                content = response["choices"][0]["message"]["content"]
            except (UnicodeDecodeError, json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
                raise RuntimeSupervisorClientError("Model returned an invalid smoke response.") from exc
            if not isinstance(content, str) or content.strip() != "READY":
                raise RuntimeSupervisorClientError(
                    "Model did not return the exact expected smoke response."
                )

            finished = self._monotonic()
            return {
                "ok": True,
                "runtime": "llamafile",
                "test": "load_and_single_inference",
                "startup_seconds": round(ready_at - started, 3),
                "inference_seconds": round(finished - inference_started, 3),
                "total_seconds": round(finished - started, 3),
                "response_excerpt": content[:300],
                "model_sha256": expected_sha,
                "systemd_unit": expected_unit,
                "supervisor_managed": True,
            }
        finally:
            # The handler removes the handoff only after exact-unit cgroup
            # teardown is verified. A failed stop leaves the durable lease.
            self._stop_exact(job_id, expected_unit)
