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
        records[record.job_id] = record

    def set_state(job_id, state):
        states.append((job_id, state))
        if job_id in records:
            old = records[job_id]
            records[job_id] = RuntimeUnitRecord(job_id, old.unit_name, state)

    supervisor = RuntimeSupervisor(
        backend=backend, persist=persist, set_state=set_state,
        load_recoverable=lambda: tuple(records.values()),
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
