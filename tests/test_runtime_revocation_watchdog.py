from types import SimpleNamespace

import pytest

from runtime_revocation_watchdog import (
    RuntimeRevocationWatchdog,
    RuntimeRevocationWatchdogError,
)


class LeaseStore:
    def __init__(self, records):
        self.records = tuple(records)
        self.fail = False

    def load_recoverable(self):
        if self.fail:
            raise OSError("broker unavailable")
        return self.records


class Authority:
    def __init__(self, decisions=None, fail=False):
        self.decisions = decisions or {}
        self.fail = fail
        self.checked = []

    def is_execution_authorized(self, job_id):
        self.checked.append(job_id)
        if self.fail:
            raise OSError("database unavailable")
        return self.decisions.get(job_id, False)


class Handlers:
    def __init__(self, *, fail_ids=()):
        self.fail_ids = set(fail_ids)
        self.stopped = []

    def stop(self, job_id):
        self.stopped.append(job_id)
        if job_id in self.fail_ids:
            raise RuntimeError("cgroup stop not confirmed")
        return {
            "job_id": job_id,
            "unit_name": f"ai-chess-arena-runtime-{job_id}.service",
            "lease_state": "stopped",
        }


def record(job_id, state="active"):
    return SimpleNamespace(
        job_id=job_id,
        unit_name=f"ai-chess-arena-runtime-{job_id}.service",
        state=state,
    )


def make_watchdog(records, *, decisions=None, authority_fail=False, fail_ids=()):
    leases = LeaseStore(records)
    authority = Authority(decisions, fail=authority_fail)
    handlers = Handlers(fail_ids=fail_ids)
    watchdog = RuntimeRevocationWatchdog(
        authority=authority, lease_store=leases, handlers=handlers,
        interval_seconds=1,
    )
    return watchdog, leases, authority, handlers


def test_watchdog_keeps_currently_authorized_active_unit():
    watchdog, _, authority, handlers = make_watchdog(
        [record(7)], decisions={7: True}
    )
    assert watchdog.run_once() == {"checked": 1, "stopped": [], "failed": []}
    assert authority.checked == [7]
    assert handlers.stopped == []


def test_watchdog_stops_unit_after_authorization_is_revoked():
    watchdog, _, authority, handlers = make_watchdog(
        [record(7)], decisions={7: False}
    )
    assert watchdog.run_once() == {"checked": 1, "stopped": [7], "failed": []}
    assert authority.checked == [7]
    assert handlers.stopped == [7]


def test_authority_database_error_fails_closed_and_requests_exact_stop():
    watchdog, _, _, handlers = make_watchdog([record(7)], authority_fail=True)
    assert watchdog.run_once() == {"checked": 1, "stopped": [7], "failed": []}
    assert handlers.stopped == [7]


def test_unconfirmed_stop_is_reported_and_lease_is_not_cleaned_by_watchdog():
    watchdog, _, _, handlers = make_watchdog(
        [record(7)], decisions={7: False}, fail_ids={7}
    )
    assert watchdog.run_once() == {"checked": 1, "stopped": [], "failed": [7]}
    assert handlers.stopped == [7]


def test_watchdog_ignores_units_inside_start_or_stop_transition():
    watchdog, _, authority, handlers = make_watchdog([
        record(7, "start_pending"), record(8, "stop_pending"),
        record(9, "recovery_stop_pending"), record(10, "active"),
    ], decisions={10: True})
    assert watchdog.run_once() == {"checked": 1, "stopped": [], "failed": []}
    assert authority.checked == [10]
    assert handlers.stopped == []


def test_watchdog_refuses_unbounded_or_invalid_configuration():
    leases = LeaseStore([])
    authority = Authority()
    handlers = Handlers()
    with pytest.raises(ValueError):
        RuntimeRevocationWatchdog(
            authority=authority, lease_store=leases, handlers=handlers,
            interval_seconds=0,
        )
    with pytest.raises(TypeError):
        RuntimeRevocationWatchdog(
            authority=object(), lease_store=leases, handlers=handlers,
        )


def test_broker_enumeration_failure_is_reported_for_retry():
    watchdog, leases, _, _ = make_watchdog([])
    leases.fail = True
    with pytest.raises(RuntimeRevocationWatchdogError, match="enumerate"):
        watchdog.run_once()


def test_watchdog_thread_checks_immediately_and_stops_cleanly():
    import threading
    import time

    stopped = threading.Event()
    class SignallingHandlers(Handlers):
        def stop(self, job_id):
            result = super().stop(job_id)
            stopped.set()
            return result

    handlers = SignallingHandlers()
    watchdog = RuntimeRevocationWatchdog(
        authority=Authority({7: False}), lease_store=LeaseStore([record(7)]),
        handlers=handlers, interval_seconds=0.1,
    )
    watchdog.start()
    try:
        assert stopped.wait(2)
    finally:
        watchdog.stop()
    assert watchdog.last_result == {"checked": 1, "stopped": [7], "failed": []}
