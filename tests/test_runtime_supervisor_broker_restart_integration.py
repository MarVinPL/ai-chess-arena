"""Supervisor restart and broker outage/recovery over real AF_UNIX sockets."""
import os
import sqlite3
import threading

import pytest

from runtime_lease_broker import (
    RuntimeLeaseBrokerClient, RuntimeLeaseBrokerError, RuntimeLeaseBrokerServer,
)
from runtime_supervisor import RuntimeSupervisor, SQLiteRuntimeUnitStore
from runtime_systemd import build_runtime_unit_spec


class FakeSystemdBackend:
    """Shared fake systemd state survives supervisor process replacement."""
    def __init__(self):
        self.active = set()
        self.starts = []
        self.stops = []

    def start(self, spec, argv):
        self.starts.append(spec.unit_name)
        self.active.add(spec.unit_name)

    def stop(self, unit_name):
        self.stops.append(unit_name)
        self.active.discard(unit_name)


def _spec(job_id):
    return build_runtime_unit_spec(
        job_id=job_id, runtime_user="arena-runtime", runtime_group="arena-runtime",
        artifact_path=f"/var/lib/arena-handoff/job-{job_id}/model.gguf",
        artifact_mount_path=f"/run/arena-runtime/job-{job_id}/model.gguf",
        output_dir=f"/run/arena-runtime/job-{job_id}",
        api_uid=max(1, os.getuid()), api_gid=max(1, os.getgid()),
        memory_max_bytes=1024**3, memory_swap_max_bytes=0,
        cpu_quota_percent=100, tasks_max=8, runtime_seconds=300,
    )


def _broker(tmp_path, store):
    socket_path = tmp_path / "broker.sock"
    server = RuntimeLeaseBrokerServer(socket_path, store=store, allowed_uid=os.getuid())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    assert server.ready.wait(2), "lease broker did not become ready"
    return server, thread, RuntimeLeaseBrokerClient(socket_path)


def _supervisor(client, backend):
    return RuntimeSupervisor(
        backend=backend, persist=client.persist,
        set_state=lambda job_id, state: client.set_state(job_id, state),
        load_recoverable=client.load_recoverable,
    )


def test_supervisor_restart_recovers_persisted_exact_unit_after_broker_restart(tmp_path):
    database = tmp_path / "leases.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE runtime_test_jobs(id INTEGER PRIMARY KEY, status TEXT, "
            "cancel_requested INTEGER NOT NULL DEFAULT 0)"
        )
        connection.execute("INSERT INTO runtime_test_jobs(id,status) VALUES(91,'running')")
    store = SQLiteRuntimeUnitStore(database)
    backend = FakeSystemdBackend()
    broker1, thread1, client1 = _broker(tmp_path, store)
    unit = "ai-chess-arena-runtime-91.service"
    try:
        first_supervisor = _supervisor(client1, backend)
        assert first_supervisor.start(
            _spec(91), ("/usr/local/lib/ai-chess-arena-runtime/runtime_worker", "--job", "91")
        ) == unit
        assert backend.active == {unit}
        assert client1.get(91).state == "active"
    finally:
        broker1.shutdown()
        thread1.join(3)
    assert not thread1.is_alive()

    # The API-owned broker is restarted against the same durable SQLite store.
    broker2, thread2, client2 = _broker(tmp_path, store)
    try:
        restarted_supervisor = _supervisor(client2, backend)
        recovery = restarted_supervisor.recover()
        assert recovery == {"stopped_units": [unit], "count": 1}
        assert backend.stops == [unit]
        assert backend.active == set()
        assert client2.get(91).state == "stopped"
        assert client2.load_recoverable() == ()
    finally:
        broker2.shutdown()
        thread2.join(3)
    assert not thread2.is_alive()


def test_broker_outage_fails_closed_then_recovers_after_restart(tmp_path):
    database = tmp_path / "leases.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE runtime_test_jobs(id INTEGER PRIMARY KEY, status TEXT, "
            "cancel_requested INTEGER NOT NULL DEFAULT 0)"
        )
        connection.execute("INSERT INTO runtime_test_jobs(id,status) VALUES(92,'running')")
    store = SQLiteRuntimeUnitStore(database)
    backend = FakeSystemdBackend()
    broker, thread, client = _broker(tmp_path, store)
    unit = "ai-chess-arena-runtime-92.service"
    try:
        supervisor = _supervisor(client, backend)
        supervisor.start(
            _spec(92), ("/usr/local/lib/ai-chess-arena-runtime/runtime_worker", "--job", "92")
        )
    finally:
        broker.shutdown()
        thread.join(3)

    unavailable_client = RuntimeLeaseBrokerClient(tmp_path / "broker.sock", timeout_seconds=0.1)
    unavailable_supervisor = _supervisor(unavailable_client, backend)
    with pytest.raises(RuntimeLeaseBrokerError, match="unavailable"):
        unavailable_supervisor.recover()
    assert backend.active == {unit}
    assert backend.stops == []  # no lease authority means no guessed stop target

    broker2, thread2, recovered_client = _broker(tmp_path, store)
    try:
        recovered_supervisor = _supervisor(recovered_client, backend)
        assert recovered_supervisor.recover()["count"] == 1
        assert backend.active == set()
        assert backend.stops == [unit]
        assert recovered_client.get(92).state == "stopped"
    finally:
        broker2.shutdown()
        thread2.join(3)


def _restartable_runtime(tmp_path, job_id):
    database = tmp_path / f"leases-{job_id}.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE runtime_test_jobs(id INTEGER PRIMARY KEY, status TEXT, "
            "cancel_requested INTEGER NOT NULL DEFAULT 0)"
        )
        connection.execute(
            "INSERT INTO runtime_test_jobs(id,status) VALUES(?,'running')", (job_id,)
        )
    store = SQLiteRuntimeUnitStore(database)
    backend = FakeSystemdBackend()
    broker, thread, client = _broker(tmp_path, store)
    return store, backend, broker, thread, client


def _restart_broker(tmp_path, store):
    broker, thread, client = _broker(tmp_path, store)
    return broker, thread, client


def test_process_loss_after_backend_start_before_active_commit_recovers_exact_unit(tmp_path):
    job_id = 93
    unit = f"ai-chess-arena-runtime-{job_id}.service"
    store, backend, broker1, thread1, client1 = _restartable_runtime(tmp_path, job_id)
    try:
        def crash_before_active_commit(requested_job, state):
            if state == "active":
                # Simulate supervisor process termination after systemd accepted
                # start but before the durable lease transition can commit.
                raise OSError("simulated supervisor process loss")
            client1.set_state(requested_job, state)

        interrupted = RuntimeSupervisor(
            backend=backend, persist=client1.persist,
            set_state=crash_before_active_commit,
            load_recoverable=client1.load_recoverable,
        )
        with pytest.raises(OSError, match="process loss"):
            interrupted.start(
                _spec(job_id),
                ("/usr/local/lib/ai-chess-arena-runtime/runtime_worker", "--job", str(job_id)),
            )
        assert backend.active == {unit}
        assert store.get(job_id).state == "start_pending"
    finally:
        broker1.shutdown()
        thread1.join(3)
    assert not thread1.is_alive()

    broker2, thread2, client2 = _restart_broker(tmp_path, store)
    try:
        restarted = _supervisor(client2, backend)
        assert restarted.recover() == {"stopped_units": [unit], "count": 1}
        assert backend.stops == [unit]
        assert backend.active == set()
        assert store.get(job_id).state == "stopped"
    finally:
        broker2.shutdown()
        thread2.join(3)
    assert not thread2.is_alive()


def test_process_loss_after_backend_stop_before_stopped_commit_retries_exact_unit(tmp_path):
    job_id = 94
    unit = f"ai-chess-arena-runtime-{job_id}.service"
    store, backend, broker1, thread1, client1 = _restartable_runtime(tmp_path, job_id)
    try:
        supervisor = _supervisor(client1, backend)
        supervisor.start(
            _spec(job_id),
            ("/usr/local/lib/ai-chess-arena-runtime/runtime_worker", "--job", str(job_id)),
        )
        record = client1.get(job_id)

        def crash_before_stopped_commit(requested_job, state):
            if state == "stopped":
                # Backend stop succeeded; process disappears before the final
                # durable acknowledgement is committed.
                raise OSError("simulated supervisor process loss")
            client1.set_state(requested_job, state)

        interrupted = RuntimeSupervisor(
            backend=backend, persist=client1.persist,
            set_state=crash_before_stopped_commit,
            load_recoverable=client1.load_recoverable,
        )
        with pytest.raises(OSError, match="process loss"):
            interrupted.stop(record)
        assert backend.active == set()
        assert store.get(job_id).state == "stop_pending"
    finally:
        broker1.shutdown()
        thread1.join(3)
    assert not thread1.is_alive()

    broker2, thread2, client2 = _restart_broker(tmp_path, store)
    try:
        restarted = _supervisor(client2, backend)
        assert restarted.recover() == {"stopped_units": [unit], "count": 1}
        # Recovery repeats an idempotent exact-unit stop because the first
        # stop's durable acknowledgement was lost with the supervisor process.
        assert backend.stops == [unit, unit]
        assert backend.active == set()
        assert store.get(job_id).state == "stopped"
    finally:
        broker2.shutdown()
        thread2.join(3)
    assert not thread2.is_alive()

class _FailAfterCommitStore:
    """Inject broker-side failure after SQLite commits but before its ACK."""
    def __init__(self, store, *, fail_action, fail_state=None):
        self.store = store
        self.fail_action = fail_action
        self.fail_state = fail_state
        self.failed = False

    def __getattr__(self, name):
        return getattr(self.store, name)

    def persist(self, record):
        self.store.persist(record)
        if self.fail_action == "persist" and not self.failed:
            self.failed = True
            raise OSError("simulated broker loss after durable persist")

    def set_state(self, job_id, state, unit_name=None):
        self.store.set_state(job_id, state, unit_name)
        if (self.fail_action == "set_state" and state == self.fail_state
                and not self.failed):
            self.failed = True
            raise OSError("simulated broker loss after durable state commit")


def test_broker_failure_after_persist_commit_never_starts_backend_and_recovers(tmp_path):
    job_id = 95
    unit = f"ai-chess-arena-runtime-{job_id}.service"
    store, backend, broker1, thread1, client1 = _restartable_runtime(tmp_path, job_id)
    broker1.store = _FailAfterCommitStore(store, fail_action="persist")
    try:
        supervisor = _supervisor(client1, backend)
        with pytest.raises(RuntimeLeaseBrokerError, match="rejected request"):
            supervisor.start(
                _spec(job_id),
                ("/usr/local/lib/ai-chess-arena-runtime/runtime_worker", "--job", str(job_id)),
            )
        # SQLite committed start_pending, but broker did not acknowledge it.
        # Fail-closed behavior means systemd must not be called.
        assert store.get(job_id).state == "start_pending"
        assert backend.starts == []
        assert backend.active == set()
    finally:
        broker1.shutdown()
        thread1.join(3)
    assert not thread1.is_alive()

    broker2, thread2, client2 = _restart_broker(tmp_path, store)
    try:
        recovered = _supervisor(client2, backend).recover()
        assert recovered == {"stopped_units": [unit], "count": 1}
        assert backend.stops == [unit]
        assert backend.active == set()
        assert store.get(job_id).state == "stopped"
    finally:
        broker2.shutdown()
        thread2.join(3)
    assert not thread2.is_alive()


def test_broker_failure_after_stop_pending_commit_skips_backend_until_recovery(tmp_path):
    job_id = 96
    unit = f"ai-chess-arena-runtime-{job_id}.service"
    store, backend, broker1, thread1, client1 = _restartable_runtime(tmp_path, job_id)
    try:
        supervisor = _supervisor(client1, backend)
        supervisor.start(
            _spec(job_id),
            ("/usr/local/lib/ai-chess-arena-runtime/runtime_worker", "--job", str(job_id)),
        )
        record = client1.get(job_id)
        broker1.store = _FailAfterCommitStore(
            store, fail_action="set_state", fail_state="stop_pending"
        )
        with pytest.raises(RuntimeLeaseBrokerError, match="rejected request"):
            supervisor.stop(record)
        # The broker committed stop_pending but failed before ACK. The
        # supervisor must not stop systemd until it can prove the transition.
        assert store.get(job_id).state == "stop_pending"
        assert backend.active == {unit}
        assert backend.stops == []
    finally:
        broker1.shutdown()
        thread1.join(3)
    assert not thread1.is_alive()

    broker2, thread2, client2 = _restart_broker(tmp_path, store)
    try:
        recovered = _supervisor(client2, backend).recover()
        assert recovered == {"stopped_units": [unit], "count": 1}
        assert backend.stops == [unit]
        assert backend.active == set()
        assert store.get(job_id).state == "stopped"
    finally:
        broker2.shutdown()
        thread2.join(3)
    assert not thread2.is_alive()
