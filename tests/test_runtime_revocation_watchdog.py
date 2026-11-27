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


def test_watchdog_revocation_racing_api_cancellation_stops_exact_unit_once():
    import threading
    from runtime_supervisor import RuntimeSupervisor, RuntimeUnitRecord

    job_id = 88
    unit = f"ai-chess-arena-runtime-{job_id}.service"
    state = {"record": RuntimeUnitRecord(job_id, unit, "active"), "stops": []}
    entered = threading.Event()
    release = threading.Event()

    class DurableLeases:
        def load_recoverable(self):
            record = state["record"]
            return (record,) if record.state in {
                "start_pending", "start_uncertain", "active", "stop_pending",
                "recovery_stop_pending",
            } else ()

    class Backend:
        def stop(self, requested_unit):
            entered.set()
            assert release.wait(2)
            state["stops"].append(requested_unit)

    leases = DurableLeases()
    supervisor = RuntimeSupervisor(
        backend=Backend(),
        persist=lambda _record: None,
        set_state=lambda _job, new_state: state.update(
            record=RuntimeUnitRecord(job_id, unit, new_state)
        ),
        load_recoverable=leases.load_recoverable,
    )

    class TrustedHandlers:
        def stop(self, requested_job):
            record = state["record"]
            supervisor.stop(record)
            return {"job_id": requested_job, "unit_name": unit,
                    "lease_state": "stopped"}

    watchdog = RuntimeRevocationWatchdog(
        authority=Authority({job_id: False}), lease_store=leases,
        handlers=TrustedHandlers(), interval_seconds=1,
    )
    watchdog_errors = []
    api_errors = []
    watchdog_thread = threading.Thread(
        target=lambda: _capture_watchdog(watchdog, watchdog_errors)
    )
    watchdog_thread.start()
    assert entered.wait(2)

    def api_cancel_stop():
        try:
            TrustedHandlers().stop(job_id)
        except Exception as exc:
            api_errors.append(exc)

    api_thread = threading.Thread(target=api_cancel_stop)
    api_thread.start()
    release.set()
    watchdog_thread.join(2)
    api_thread.join(2)
    assert not watchdog_thread.is_alive() and not api_thread.is_alive()
    assert state["stops"] == [unit]
    assert state["record"].state == "stopped"
    assert watchdog.last_result == {"checked": 1, "stopped": [job_id], "failed": []}
    assert len(api_errors) == 1
    assert watchdog_errors == []


def _capture_watchdog(watchdog, errors):
    try:
        watchdog.run_once()
    except Exception as exc:
        errors.append(exc)


def test_revocation_during_blocking_start_is_checked_immediately_after_activation():
    import os
    import threading
    from runtime_supervisor import RuntimeSupervisor, RuntimeUnitRecord
    from runtime_systemd import build_runtime_unit_spec

    job_id = 89
    unit = f"ai-chess-arena-runtime-{job_id}.service"
    state = {"record": None, "revoked": False, "started": [], "stopped": []}
    backend_entered = threading.Event()
    release_backend = threading.Event()

    class BlockingBackend:
        def start(self, runtime_spec, argv):
            backend_entered.set()
            assert release_backend.wait(2)
            state["started"].append(runtime_spec.unit_name)

        def stop(self, unit_name):
            state["stopped"].append(unit_name)

    def persist(record):
        assert state["record"] is None
        state["record"] = record

    def set_state(requested_job, new_state):
        current = state["record"]
        assert requested_job == job_id and current is not None
        state["record"] = RuntimeUnitRecord(job_id, current.unit_name, new_state)

    def load_recoverable():
        record = state["record"]
        return (record,) if record and record.state in {
            "start_pending", "start_uncertain", "active", "stop_pending",
            "recovery_stop_pending",
        } else ()

    supervisor = RuntimeSupervisor(
        backend=BlockingBackend(), persist=persist, set_state=set_state,
        load_recoverable=load_recoverable,
    )

    class Authority:
        def is_execution_authorized(self, requested_job):
            assert requested_job == job_id
            return not state["revoked"]

    class TrustedHandlers:
        def stop(self, requested_job):
            record = state["record"]
            supervisor.stop(record)
            return {"job_id": requested_job, "unit_name": record.unit_name,
                    "lease_state": "stopped"}

    watchdog = RuntimeRevocationWatchdog(
        authority=Authority(), lease_store=SimpleNamespace(load_recoverable=load_recoverable),
        handlers=TrustedHandlers(), interval_seconds=1,
    )
    runtime_spec = build_runtime_unit_spec(
        job_id=job_id, runtime_user="arena-runtime", runtime_group="arena-runtime",
        artifact_path=f"/var/lib/arena-handoff/job-{job_id}/model.gguf",
        artifact_mount_path=f"/run/arena-runtime/job-{job_id}/model.gguf",
        output_dir=f"/run/arena-runtime/job-{job_id}",
        api_uid=os.getuid(), api_gid=os.getgid(), memory_max_bytes=1024**3,
        memory_swap_max_bytes=0, cpu_quota_percent=100, tasks_max=8,
        runtime_seconds=300,
    )
    start_errors = []

    def start():
        try:
            supervisor.start(
                runtime_spec,
                ("/usr/local/lib/ai-chess-arena-runtime/runtime_worker", "--job", str(job_id)),
                before_start=lambda: None,
            )
        except Exception as exc:
            start_errors.append(exc)

    thread = threading.Thread(target=start)
    thread.start()
    assert backend_entered.wait(2)
    assert state["record"].state == "start_pending"

    # Revocation while systemd start is still in flight must not race an
    # unconfirmed unit; it is acted on as soon as start publishes active.
    state["revoked"] = True
    assert watchdog.run_once() == {"checked": 0, "stopped": [], "failed": []}
    assert state["stopped"] == []

    release_backend.set()
    thread.join(2)
    assert not thread.is_alive()
    assert start_errors == []
    assert state["record"].state == "active"

    assert watchdog.run_once() == {"checked": 1, "stopped": [job_id], "failed": []}
    assert state["started"] == [unit]
    assert state["stopped"] == [unit]
    assert state["record"].state == "stopped"
