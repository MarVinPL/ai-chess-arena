import os
import pytest

from runtime_supervisor import RuntimeSupervisor, RuntimeSupervisorError, RuntimeUnitRecord
from runtime_systemd import build_runtime_unit_spec


class FakeBackend:
    def __init__(self, fail_start=False):
        self.started = []
        self.stopped = []
        self.fail_start = fail_start

    def start(self, spec, argv):
        self.started.append((spec.unit_name, argv))
        if self.fail_start:
            raise OSError("simulated failure after start request")

    def stop(self, unit_name):
        self.stopped.append(unit_name)


def spec(job_id=7):
    return build_runtime_unit_spec(
        job_id=job_id, runtime_user="arena-runtime", runtime_group="arena-runtime",
        artifact_path=f"/var/lib/arena-handoff/job-{job_id}/model.gguf",
        artifact_mount_path=f"/run/arena-runtime/job-{job_id}/model.gguf",
        output_dir=f"/run/arena-runtime/job-{job_id}",
        api_uid=os.getuid(), api_gid=os.getgid(),
        memory_max_bytes=1024**3, memory_swap_max_bytes=0,
        cpu_quota_percent=100, tasks_max=8, runtime_seconds=300,
    )


def harness(backend, records=None, fail_persist=False):
    records = records if records is not None else {}
    states = []

    def persist(record):
        if fail_persist:
            raise OSError("durable store unavailable")
        existing = records.get(record.job_id)
        if existing is not None:
            if (existing.unit_name == record.unit_name
                    and existing.state == "start_pending"
                    and record.state == "start_pending"):
                return
            raise RuntimeSupervisorError("duplicate durable lease")
        records[record.job_id] = record

    def set_state(job_id, state):
        states.append((job_id, state))
        if job_id in records:
            old = records[job_id]
            records[job_id] = RuntimeUnitRecord(job_id, old.unit_name, state)

    supervisor = RuntimeSupervisor(
        backend=backend, persist=persist, set_state=set_state,
        load_recoverable=lambda: tuple(
            record for record in records.values()
            if record.state in {"start_pending", "start_uncertain", "active",
                                "stop_pending", "recovery_stop_pending"}
        ),
    )
    return supervisor, records, states


def test_unit_identity_is_durably_persisted_before_backend_start():
    order = []
    backend = FakeBackend()
    supervisor = RuntimeSupervisor(
        backend=backend,
        persist=lambda record: order.append(("persist", record.unit_name)),
        set_state=lambda job_id, state: order.append(("state", state)),
        load_recoverable=lambda: (),
    )
    name = supervisor.start(spec(), ("/usr/local/lib/ai-chess-arena-runtime/runtime_worker", "--job", "7"))
    assert name == "ai-chess-arena-runtime-7.service"
    assert order[0] == ("persist", name)
    assert backend.started == [(name, ("/usr/local/lib/ai-chess-arena-runtime/runtime_worker", "--job", "7"))]


def test_persistence_failure_never_calls_backend():
    backend = FakeBackend()
    supervisor, _, _ = harness(backend, fail_persist=True)
    with pytest.raises(OSError):
        supervisor.start(spec(), ("/usr/local/lib/ai-chess-arena-runtime/runtime_worker", "--job", "7"))
    assert backend.started == []


def test_ambiguous_start_is_marked_recoverable_and_recovery_stops_exact_unit():
    backend = FakeBackend(fail_start=True)
    supervisor, records, _ = harness(backend)
    with pytest.raises(OSError):
        supervisor.start(spec(11), ("/usr/local/lib/ai-chess-arena-runtime/runtime_worker", "--job", "11"))
    assert records[11].unit_name == "ai-chess-arena-runtime-11.service"
    assert records[11].state == "start_uncertain"
    backend.fail_start = False
    result = supervisor.recover()
    assert result == {"stopped_units": ["ai-chess-arena-runtime-11.service"], "count": 1}
    assert backend.stopped == ["ai-chess-arena-runtime-11.service"]
    assert records[11].state == "stopped"


def test_recovery_never_targets_unrelated_or_mismatched_unit():
    backend = FakeBackend()
    bad = RuntimeUnitRecord(5, "ssh.service", "active")
    supervisor = RuntimeSupervisor(backend=backend, persist=lambda r: None,
                                   set_state=lambda i, s: None,
                                   load_recoverable=lambda: (bad,))
    with pytest.raises(RuntimeSupervisorError):
        supervisor.recover()
    assert backend.stopped == []


def test_recovery_uses_only_persisted_exact_units():
    backend = FakeBackend()
    supervisor, records, _ = harness(backend, {
        3: RuntimeUnitRecord(3, "ai-chess-arena-runtime-3.service", "active"),
        4: RuntimeUnitRecord(4, "ai-chess-arena-runtime-4.service", "start_uncertain"),
    })
    assert supervisor.recover()["count"] == 2
    assert backend.stopped == ["ai-chess-arena-runtime-3.service", "ai-chess-arena-runtime-4.service"]
    assert all(record.state == "stopped" for record in records.values())


@pytest.mark.parametrize("argv", [(), ("relative-worker",), ("/bin/true", "bad\x00arg")])
def test_invalid_worker_command_fails_before_persistence_or_backend(argv):
    backend = FakeBackend()
    supervisor, records, _ = harness(backend)
    with pytest.raises(RuntimeSupervisorError):
        supervisor.start(spec(), argv)
    assert records == {}
    assert backend.started == []


def test_sqlite_store_commits_unit_identity_and_recovers_only_incomplete_leases(tmp_path):
    import sqlite3
    from runtime_supervisor import SQLiteRuntimeUnitStore

    db = tmp_path / "arena.sqlite3"
    with sqlite3.connect(db) as connection:
        connection.execute("CREATE TABLE runtime_test_jobs(id INTEGER PRIMARY KEY, status TEXT, cancel_requested INTEGER NOT NULL DEFAULT 0)")
        connection.executemany("INSERT INTO runtime_test_jobs(id,status) VALUES(?,?)",
                               [(21, "running"), (22, "queued")])
    store = SQLiteRuntimeUnitStore(db)
    store.persist(RuntimeUnitRecord(21, "ai-chess-arena-runtime-21.service", "start_pending"))
    store.set_state(21, "active")
    assert store.load_recoverable() == (
        RuntimeUnitRecord(21, "ai-chess-arena-runtime-21.service", "active"),
    )
    store.set_state(21, "stop_pending")
    store.set_state(21, "stopped")
    assert store.load_recoverable() == ()
    with pytest.raises(RuntimeSupervisorError):
        store.persist(RuntimeUnitRecord(22, "ai-chess-arena-runtime-22.service", "start_pending"))


def test_sqlite_store_rejects_duplicate_or_unrelated_unit_identity(tmp_path):
    import sqlite3
    from runtime_supervisor import SQLiteRuntimeUnitStore

    db = tmp_path / "arena.sqlite3"
    with sqlite3.connect(db) as connection:
        connection.execute("CREATE TABLE runtime_test_jobs(id INTEGER PRIMARY KEY, status TEXT, cancel_requested INTEGER NOT NULL DEFAULT 0)")
        connection.execute("INSERT INTO runtime_test_jobs(id,status) VALUES(31,'running')")
    store = SQLiteRuntimeUnitStore(db)
    store.persist(RuntimeUnitRecord(31, "ai-chess-arena-runtime-31.service", "start_pending"))
    # An identical retry is safe while the lease remains start_pending.
    store.persist(RuntimeUnitRecord(31, "ai-chess-arena-runtime-31.service", "start_pending"))
    with pytest.raises(RuntimeSupervisorError):
        store.persist(RuntimeUnitRecord(31, "ai-chess-arena-runtime-32.service", "start_pending"))
    with pytest.raises(RuntimeSupervisorError):
        store.persist(RuntimeUnitRecord(99, "ai-chess-arena-runtime-99.service", "start_pending"))


def test_supervisor_rejects_spec_with_weakened_network_or_identity_policy():
    from dataclasses import replace
    safe = spec()
    weakened = replace(safe, properties=tuple(
        (key, "no" if key == "PrivateNetwork" else value)
        for key, value in safe.properties
    ))
    backend = FakeBackend()
    supervisor, records, _ = harness(backend)
    with pytest.raises(RuntimeSupervisorError):
        supervisor.start(weakened, ("/usr/bin/true",))
    assert records == {}
    assert backend.started == []


def test_stop_requires_a_persisted_lease():
    backend = FakeBackend()
    supervisor, _, _ = harness(backend)
    record = RuntimeUnitRecord(7, "ai-chess-arena-runtime-7.service", "active")
    with pytest.raises(RuntimeSupervisorError):
        supervisor.stop(record)
    assert backend.stopped == []


def test_sqlite_store_refuses_cancelled_running_job(tmp_path):
    import sqlite3
    from runtime_supervisor import SQLiteRuntimeUnitStore

    db = tmp_path / "arena.sqlite3"
    with sqlite3.connect(db) as connection:
        connection.execute(
            "CREATE TABLE runtime_test_jobs(id INTEGER PRIMARY KEY, status TEXT, cancel_requested INTEGER NOT NULL DEFAULT 0)"
        )
        connection.execute(
            "INSERT INTO runtime_test_jobs(id,status,cancel_requested) VALUES(51,'running',1)"
        )
    store = SQLiteRuntimeUnitStore(db)
    with pytest.raises(RuntimeSupervisorError, match="nieanulowanego"):
        store.persist(RuntimeUnitRecord(51, "ai-chess-arena-runtime-51.service", "start_pending"))


def test_production_lease_store_requires_preprovisioned_schema_without_migrating(tmp_path):
    import sqlite3
    from runtime_supervisor import SQLiteRuntimeUnitStore

    database = tmp_path / "arena.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE runtime_test_jobs(id INTEGER PRIMARY KEY)")
    with pytest.raises(RuntimeSupervisorError, match="schema is incomplete"):
        SQLiteRuntimeUnitStore(database, initialize_schema=False)
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='runtime_unit_leases'"
        ).fetchone() is None


def test_production_lease_store_opens_existing_schema_read_write_without_create(tmp_path):
    import sqlite3
    from runtime_supervisor import SQLiteRuntimeUnitStore

    database = tmp_path / "arena.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE runtime_unit_leases(job_id INTEGER PRIMARY KEY, unit_name TEXT NOT NULL UNIQUE, state TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
    store = SQLiteRuntimeUnitStore(database, initialize_schema=False)
    assert store.load_recoverable() == ()


def test_production_lease_store_refuses_missing_database(tmp_path):
    from runtime_supervisor import SQLiteRuntimeUnitStore

    with pytest.raises(RuntimeSupervisorError, match="schema is unavailable"):
        SQLiteRuntimeUnitStore(tmp_path / "missing.sqlite3", initialize_schema=False)


def test_sqlite_lease_persist_is_idempotent_only_for_same_pending_unit(tmp_path):
    import sqlite3
    from runtime_supervisor import SQLiteRuntimeUnitStore

    db = tmp_path / "arena.sqlite3"
    with sqlite3.connect(db) as connection:
        connection.execute("CREATE TABLE runtime_test_jobs(id INTEGER PRIMARY KEY, status TEXT, cancel_requested INTEGER NOT NULL DEFAULT 0)")
        connection.execute("INSERT INTO runtime_test_jobs(id,status) VALUES(71,'running')")
    store = SQLiteRuntimeUnitStore(db)
    record = RuntimeUnitRecord(71, "ai-chess-arena-runtime-71.service", "start_pending")
    store.persist(record)
    store.persist(record)
    assert store.get(71) == record
    with pytest.raises(RuntimeSupervisorError):
        store.persist(RuntimeUnitRecord(71, "ai-chess-arena-runtime-72.service", "start_pending"))
    store.set_state(71, "active")
    with pytest.raises(RuntimeSupervisorError):
        store.persist(record)


def test_sqlite_lease_state_update_checks_exact_unit_and_transition(tmp_path):
    import sqlite3
    from runtime_supervisor import SQLiteRuntimeUnitStore

    db = tmp_path / "arena.sqlite3"
    with sqlite3.connect(db) as connection:
        connection.execute("CREATE TABLE runtime_test_jobs(id INTEGER PRIMARY KEY, status TEXT, cancel_requested INTEGER NOT NULL DEFAULT 0)")
        connection.execute("INSERT INTO runtime_test_jobs(id,status) VALUES(81,'running')")
    store = SQLiteRuntimeUnitStore(db)
    store.persist(RuntimeUnitRecord(81, "ai-chess-arena-runtime-81.service", "start_pending"))
    with pytest.raises(RuntimeSupervisorError):
        store.set_state(81, "active", "ai-chess-arena-runtime-82.service")
    store.set_state(81, "active", "ai-chess-arena-runtime-81.service")
    with pytest.raises(RuntimeSupervisorError):
        store.set_state(81, "start_pending", "ai-chess-arena-runtime-81.service")
    assert store.get(81).state == "active"


def test_stop_failure_keeps_exact_lease_recoverable_across_supervisor_restart():
    class StopFailsAfterSideEffect(FakeBackend):
        def __init__(self):
            super().__init__()
            self.running = set()
            self.fail_stop_once = True

        def start(self, runtime_spec, argv):
            super().start(runtime_spec, argv)
            self.running.add(runtime_spec.unit_name)

        def stop(self, unit_name):
            self.stopped.append(unit_name)
            self.running.discard(unit_name)
            if self.fail_stop_once:
                self.fail_stop_once = False
                raise TimeoutError("stop completed but acknowledgement was lost")

    backend = StopFailsAfterSideEffect()
    supervisor, records, _ = harness(backend)
    unit = supervisor.start(
        spec(61), ("/usr/local/lib/ai-chess-arena-runtime/runtime_worker", "--job", "61")
    )
    assert unit in backend.running

    with pytest.raises(TimeoutError):
        supervisor.stop(records[61])
    assert records[61].state == "stop_pending"
    assert unit not in backend.running

    # A fresh supervisor instance sees the same durable lease and confirms the
    # exact unit is stopped; it never invents or enumerates another target.
    restarted, _, _ = harness(backend, records)
    assert restarted.recover() == {"stopped_units": [unit], "count": 1}
    assert records[61].state == "stopped"
    assert backend.stopped == [unit, unit]


def test_recovery_stop_failure_retains_lease_for_a_later_recovery_attempt():
    class StopFailsOnce(FakeBackend):
        def __init__(self):
            super().__init__()
            self.fail = True

        def stop(self, unit_name):
            self.stopped.append(unit_name)
            if self.fail:
                self.fail = False
                raise OSError("systemd stop response unavailable")

    backend = StopFailsOnce()
    record = RuntimeUnitRecord(62, "ai-chess-arena-runtime-62.service", "active")
    supervisor, records, _ = harness(backend, {62: record})
    with pytest.raises(OSError):
        supervisor.recover()
    assert records[62].state == "recovery_stop_pending"

    restarted, _, _ = harness(backend, records)
    assert restarted.recover() == {"stopped_units": [record.unit_name], "count": 1}
    assert records[62].state == "stopped"
    assert backend.stopped == [record.unit_name, record.unit_name]


def test_concurrent_duplicate_start_serializes_and_never_starts_same_unit_twice():
    import threading

    entered = threading.Event()
    release = threading.Event()
    class BlockingBackend(FakeBackend):
        def start(self, runtime_spec, argv):
            entered.set()
            assert release.wait(2)
            super().start(runtime_spec, argv)

    backend = BlockingBackend()
    supervisor, records, _ = harness(backend)
    argv = ("/usr/local/lib/ai-chess-arena-runtime/runtime_worker", "--job", "63")
    outcomes = []
    def invoke():
        try:
            outcomes.append(("ok", supervisor.start(spec(63), argv)))
        except Exception as exc:
            outcomes.append(("error", exc))

    first = threading.Thread(target=invoke)
    second = threading.Thread(target=invoke)
    first.start()
    assert entered.wait(2)
    second.start()
    release.set()
    first.join(2)
    second.join(2)
    assert not first.is_alive() and not second.is_alive()
    assert len(backend.started) == 1
    assert records[63].state == "active"
    assert [kind for kind, _ in outcomes].count("ok") == 1
    assert [kind for kind, _ in outcomes].count("error") == 1


def test_concurrent_stop_requests_call_backend_once_and_keep_terminal_lease():
    import threading

    entered = threading.Event()
    release = threading.Event()
    class BlockingBackend(FakeBackend):
        def stop(self, unit_name):
            entered.set()
            assert release.wait(2)
            super().stop(unit_name)

    backend = BlockingBackend()
    supervisor, records, _ = harness(backend)
    unit = supervisor.start(
        spec(64), ("/usr/local/lib/ai-chess-arena-runtime/runtime_worker", "--job", "64")
    )
    record = records[64]
    outcomes = []
    def invoke():
        try:
            supervisor.stop(record)
            outcomes.append("ok")
        except Exception:
            outcomes.append("error")

    first = threading.Thread(target=invoke)
    second = threading.Thread(target=invoke)
    first.start()
    assert entered.wait(2)
    second.start()
    release.set()
    first.join(2)
    second.join(2)
    assert not first.is_alive() and not second.is_alive()
    assert backend.stopped == [unit]
    assert records[64].state == "stopped"
    assert sorted(outcomes) == ["error", "ok"]


def test_recovery_and_stop_race_cannot_double_stop_same_unit():
    import threading

    entered = threading.Event()
    release = threading.Event()
    class BlockingBackend(FakeBackend):
        def stop(self, unit_name):
            entered.set()
            assert release.wait(2)
            super().stop(unit_name)

    backend = BlockingBackend()
    supervisor, records, _ = harness(backend)
    unit = supervisor.start(
        spec(65), ("/usr/local/lib/ai-chess-arena-runtime/runtime_worker", "--job", "65")
    )
    record = records[65]
    recovery_results = []
    stopper = threading.Thread(target=lambda: supervisor.stop(record))
    recovery = threading.Thread(target=lambda: recovery_results.append(supervisor.recover()))
    stopper.start()
    assert entered.wait(2)
    recovery.start()
    release.set()
    stopper.join(2)
    recovery.join(2)
    assert not stopper.is_alive() and not recovery.is_alive()
    assert backend.stopped == [unit]
    assert records[65].state == "stopped"
    assert recovery_results == [{"stopped_units": [], "count": 0}]
