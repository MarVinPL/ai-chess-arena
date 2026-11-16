import hashlib
import os
import sqlite3

import pytest

from dataclasses import replace

from runtime_supervisor import RuntimeSupervisor, SQLiteRuntimeUnitStore
from runtime_systemd import build_runtime_unit_spec
from runtime_supervisor_authority import RuntimeJobAuthorization
from runtime_supervisor_handlers import (
    RuntimeSupervisorHandlerError,
    RuntimeSupervisorHandlers,
)


class FakeBackend:
    def __init__(self, store=None, fail_start=False):
        self.store = store
        self.fail_start = fail_start
        self.started = []
        self.stopped = []

    def start(self, spec, argv):
        if self.store is not None:
            lease = self.store.get(int(argv[-1]))
            assert lease is not None and lease.state == "start_pending"
        self.started.append((spec, argv))
        if self.fail_start:
            raise OSError("ambiguous simulated systemd start")

    def stop(self, unit_name):
        self.stopped.append(unit_name)


class FakeAuthority:
    def __init__(self, auth, fail_on_call=None, changed_on_second=False):
        self.auth = auth
        self.calls = 0
        self.fail_on_call = fail_on_call
        self.changed_on_second = changed_on_second

    def resolve_start(self, job_id):
        self.calls += 1
        if self.fail_on_call == self.calls:
            raise PermissionError("permission revoked")
        if self.changed_on_second and self.calls == 2:
            return RuntimeJobAuthorization(
                **{**self.auth.__dict__, "artifact_sha256": "c" * 64}
            )
        return self.auth


def harness(tmp_path, *, fail_start=False, authority_options=None, persist_override=None):
    db = tmp_path / "arena.sqlite3"
    with sqlite3.connect(db) as connection:
        connection.execute("CREATE TABLE runtime_test_jobs(id INTEGER PRIMARY KEY,status TEXT)")
        connection.execute("INSERT INTO runtime_test_jobs VALUES(7,'running')")
    store = SQLiteRuntimeUnitStore(db)
    backend = FakeBackend(store=store, fail_start=fail_start)
    supervisor = RuntimeSupervisor(
        backend=backend,
        persist=persist_override or store.persist,
        set_state=store.set_state,
        load_recoverable=store.load_recoverable,
    )

    quarantine = tmp_path / "quarantine"
    quarantine.mkdir(mode=0o700)
    source_dir = quarantine / "download"
    source_dir.mkdir(mode=0o700)
    source = source_dir / "model.quarantine"
    content = b"verified artifact for supervisor handler"
    source.write_bytes(content)

    handoff = tmp_path / "handoff"
    handoff.mkdir(mode=0o710)
    os.chmod(handoff, 0o710)
    os.chown(handoff, os.getuid(), os.getgid())

    auth = RuntimeJobAuthorization(
        job_id=7, model_id=3, repo_id="org/model", revision="a" * 40,
        artifact_key=f"hf:org/model|{'a' * 40}|model.gguf",
        artifact_sha256=hashlib.sha256(content).hexdigest(),
        source_path=str(source), size_bytes=len(content),
        permission_request_id=9, batch_id=4, timeout_seconds=120,
        authorized_at="2030-01-01T00:00:00Z",
    )
    authority = FakeAuthority(auth, **(authority_options or {}))
    def test_spec_builder(auth, handoff_result):
        memory_max = int(auth.size_bytes * 1.5) + 1536 * 1024**2
        base = build_runtime_unit_spec(
            job_id=auth.job_id, runtime_user="arena-runtime", runtime_group="arena-runtime",
            artifact_path=f"/var/lib/arena-handoff/job-{auth.job_id}/model.gguf",
            output_dir=f"/run/arena-runtime/job-{auth.job_id}",
            memory_max_bytes=memory_max, memory_swap_max_bytes=0,
            cpu_quota_percent=100, tasks_max=8, runtime_seconds=auth.timeout_seconds,
        )
        properties = tuple(
            (key, value.replace(
                f"/var/lib/arena-handoff/job-{auth.job_id}/model.gguf",
                handoff_result.path,
            ) if key == "ReadOnlyPaths" else value)
            for key, value in base.properties
        )
        return replace(base, properties=properties)

    handlers = RuntimeSupervisorHandlers(
        authority=authority, supervisor=supervisor, lease_store=store,
        quarantine_root=quarantine, handoff_root=handoff,
        runtime_gid=os.getgid(), supervisor_uid=os.getuid(),
        spec_builder=test_spec_builder,
    )
    return handlers, store, backend, authority, handoff


def test_start_persists_exact_lease_before_backend_and_keeps_handoff(tmp_path):
    handlers, store, backend, authority, handoff = harness(tmp_path)
    result = handlers.start(7)
    assert result == {
        "job_id": 7,
        "unit_name": "ai-chess-arena-runtime-7.service",
        "lease_state": "active",
        "unit_state": "start_requested",
    }
    assert authority.calls == 2
    assert store.get(7).state == "active"
    assert backend.started[0][1] == (
        "/usr/local/lib/ai-chess-arena-runtime/runtime_worker", "--job", "7"
    )
    assert (handoff / "job-7" / "model.gguf").is_file()


def test_stop_targets_persisted_unit_then_removes_handoff(tmp_path):
    handlers, store, backend, _, handoff = harness(tmp_path)
    handlers.start(7)
    result = handlers.stop(7)
    assert result["lease_state"] == "stopped"
    assert backend.stopped == ["ai-chess-arena-runtime-7.service"]
    assert store.get(7).state == "stopped"
    assert not (handoff / "job-7").exists()


def test_ambiguous_backend_start_keeps_handoff_for_recovery(tmp_path):
    handlers, store, backend, _, handoff = harness(tmp_path, fail_start=True)
    with pytest.raises(RuntimeSupervisorHandlerError, match="uncertain"):
        handlers.start(7)
    assert store.get(7).state == "start_uncertain"
    assert (handoff / "job-7" / "model.gguf").is_file()
    result = handlers.recover()
    assert result["count"] == 1
    assert result["cleaned_handoffs"] == [7]
    assert backend.stopped == ["ai-chess-arena-runtime-7.service"]
    assert not (handoff / "job-7").exists()


def test_second_authority_check_failure_cleans_unleased_handoff(tmp_path):
    handlers, store, backend, _, handoff = harness(
        tmp_path, authority_options={"fail_on_call": 2}
    )
    with pytest.raises(RuntimeSupervisorHandlerError):
        handlers.start(7)
    assert store.get(7) is None
    assert backend.started == []
    assert not (handoff / "job-7").exists()


def test_changed_authorization_during_handoff_is_rejected_and_cleaned(tmp_path):
    handlers, store, backend, _, handoff = harness(
        tmp_path, authority_options={"changed_on_second": True}
    )
    with pytest.raises(RuntimeSupervisorHandlerError, match="changed"):
        handlers.start(7)
    assert store.get(7) is None
    assert backend.started == []
    assert not (handoff / "job-7").exists()


def test_persistence_failure_never_starts_backend_and_cleans_handoff(tmp_path):
    def fail_persist(_record):
        raise OSError("lease database unavailable")
    handlers, store, backend, _, handoff = harness(
        tmp_path, persist_override=fail_persist
    )
    with pytest.raises(RuntimeSupervisorHandlerError):
        handlers.start(7)
    assert store.get(7) is None
    assert backend.started == []
    assert not (handoff / "job-7").exists()


def test_status_reports_durable_lease_not_claimed_live_systemd_state(tmp_path):
    handlers, store, _, _, _ = harness(tmp_path)
    assert handlers.status(7) == {
        "job_id": 7, "lease_exists": False, "lease_state": "absent"
    }
    handlers.start(7)
    status = handlers.status(7)
    assert status["lease_state"] == "active"
    assert status["unit_name"] == "ai-chess-arena-runtime-7.service"


def test_stop_without_durable_lease_is_refused(tmp_path):
    handlers, _, backend, _, _ = harness(tmp_path)
    with pytest.raises(RuntimeSupervisorHandlerError, match="No durable"):
        handlers.stop(7)
    assert backend.stopped == []


def test_recovery_removes_handoff_without_lease_because_unit_start_requires_prior_lease(tmp_path):
    handlers, _, _, _, handoff = harness(tmp_path)
    handlers.start(7)
    # Simulate a stale unleased handoff belonging to a different job ID.
    orphan = handoff / "job-8"
    orphan.mkdir(mode=0o750)
    os.chown(orphan, os.getuid(), os.getgid())
    (orphan / "model.gguf").write_bytes(b"orphan")
    os.chmod(orphan / "model.gguf", 0o440)
    os.chown(orphan / "model.gguf", os.getuid(), os.getgid())
    result = handlers.recover()
    assert 8 in result["cleaned_handoffs"]
    assert not orphan.exists()
    # The active job is stopped by recovery and cleaned too.
    assert 7 in result["cleaned_handoffs"]


def test_narrow_ipc_round_trip_uses_trusted_start_stop_status_handlers(tmp_path):
    import threading
    import time
    from runtime_supervisor_ipc import supervisor_request

    handlers, store, backend, _, _ = harness(tmp_path)
    socket_path = tmp_path / "supervisor.sock"
    server = handlers.make_ipc_server(socket_path, allowed_uid=os.getuid())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    deadline = time.monotonic() + 2
    while not socket_path.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    try:
        assert supervisor_request(socket_path, "status", 7) == {
            "job_id": 7, "lease_exists": False, "lease_state": "absent"
        }
        started = supervisor_request(socket_path, "start", 7)
        assert started["unit_state"] == "start_requested"
        assert supervisor_request(socket_path, "status", 7)["lease_state"] == "active"
        stopped = supervisor_request(socket_path, "stop", 7)
        assert stopped["lease_state"] == "stopped"
        assert backend.stopped == ["ai-chess-arena-runtime-7.service"]
    finally:
        server.shutdown()
        thread.join(timeout=2)
