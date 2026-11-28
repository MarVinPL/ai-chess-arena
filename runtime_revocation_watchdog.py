"""Fail-closed revocation watchdog for active supervised runtime units."""
from __future__ import annotations

import threading
from typing import Callable


class RuntimeRevocationWatchdogError(RuntimeError):
    """Raised when the watchdog cannot safely enumerate durable leases."""


class RuntimeRevocationWatchdog:
    """Periodically stop active units whose current authorization no longer holds.

    Only active and start-uncertain leases are considered. A start_pending lease
    may still be inside the supervisor's final authorization/start critical path;
    it is checked as soon as that path publishes active or start_uncertain.
    Failed authorization reads are treated as revocation (fail closed). Failed
    stops retain their durable lease and handoff for supervisor recovery.
    """

    _WATCHED_STATES = frozenset({"active", "start_uncertain"})

    def __init__(self, *, authority, lease_store, handlers,
                 interval_seconds: float = 1.0,
                 on_error: Callable[[str], None] | None = None):
        if not callable(getattr(authority, "is_execution_authorized", None)):
            raise TypeError("A live execution authority is required.")
        if not callable(getattr(lease_store, "load_recoverable", None)):
            raise TypeError("A durable recoverable lease store is required.")
        if not callable(getattr(handlers, "stop", None)):
            raise TypeError("A trusted exact-unit stop handler is required.")
        if isinstance(interval_seconds, bool) or not isinstance(interval_seconds, (int, float)):
            raise ValueError("Watchdog interval must be numeric.")
        if not 0.1 <= interval_seconds <= 30:
            raise ValueError("Watchdog interval must be between 0.1 and 30 seconds.")
        if on_error is not None and not callable(on_error):
            raise TypeError("Watchdog error callback must be callable.")
        self._authority = authority
        self._leases = lease_store
        self._handlers = handlers
        self._interval = float(interval_seconds)
        self._on_error = on_error
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_result = {"checked": 0, "stopped": [], "failed": []}

    def run_once(self) -> dict:
        try:
            records = tuple(self._leases.load_recoverable())
        except Exception as exc:
            raise RuntimeRevocationWatchdogError(
                "Cannot enumerate durable runtime leases."
            ) from exc
        checked = 0
        stopped = []
        failed = []
        for record in records:
            if getattr(record, "state", None) not in self._WATCHED_STATES:
                continue
            checked += 1
            try:
                authorized = self._authority.is_execution_authorized(record.job_id)
            except Exception:
                # A DB/authority outage must not leave a model running without
                # proof that the current grant and cancellation state are valid.
                authorized = False
            if authorized:
                continue
            try:
                result = self._handlers.stop(record.job_id)
                if (not isinstance(result, dict)
                        or result.get("job_id") != record.job_id
                        or result.get("unit_name") != record.unit_name
                        or result.get("lease_state") != "stopped"):
                    raise RuntimeError("Exact-unit stop was not confirmed.")
                stopped.append(record.job_id)
            except Exception:
                # Do not delete leases or handoffs here; startup recovery owns
                # cleanup after the backend proves the cgroup is empty.
                failed.append(record.job_id)
        self.last_result = {"checked": checked, "stopped": stopped, "failed": failed}
        return dict(self.last_result)

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                result = self.run_once()
                if result["failed"] and self._on_error:
                    self._on_error("One or more revoked runtime units could not be stopped.")
            except Exception:
                if self._on_error:
                    self._on_error("Runtime lease enumeration failed; watchdog will retry.")
            if self._stop_event.wait(self._interval):
                break

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, name="arena-runtime-revocation-watchdog", daemon=True
        )
        self._thread.start()

    def stop(self, *, timeout_seconds: float = 5.0) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout_seconds)
            if thread.is_alive():
                raise RuntimeRevocationWatchdogError("Watchdog thread did not stop cleanly.")
            self._thread = None
