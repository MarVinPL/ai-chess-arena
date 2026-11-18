from pathlib import Path
import os
import sqlite3
import threading

import pytest

from runtime_lease_broker import (
    MAX_RECOVERABLE_RECORDS,
    RuntimeLeaseBrokerClient,
    RuntimeLeaseBrokerError,
    RuntimeLeaseBrokerServer,
    _validate_request,
)
from runtime_supervisor import RuntimeUnitRecord, SQLiteRuntimeUnitStore


def _store(tmp_path, count=1):
    database = tmp_path / "arena.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE runtime_test_jobs(id INTEGER PRIMARY KEY, status TEXT, "
            "cancel_requested INTEGER NOT NULL DEFAULT 0)"
        )
        connection.executemany(
            "INSERT INTO runtime_test_jobs(id,status) VALUES(?,'running')",
            [(number,) for number in range(1, count + 1)],
        )
    return SQLiteRuntimeUnitStore(database)


def test_broker_request_schema_is_strict_and_duplicate_keys_rejected():
    valid = b'{"action":"persist","job_id":7,"unit_name":"ai-chess-arena-runtime-7.service","state":"start_pending"}\n'
    assert _validate_request(valid)["job_id"] == 7
    invalid = [
        b'{"action":"persist","job_id":7,"unit_name":"ai-chess-arena-runtime-8.service","state":"start_pending"}\n',
        b'{"action":"persist","job_id":true,"unit_name":"ai-chess-arena-runtime-1.service","state":"start_pending"}\n',
        b'{"action":"set_state","job_id":7,"unit_name":"ai-chess-arena-runtime-7.service","state":"deleted"}\n',
        b'{"action":"get","job_id":7,"sql":"DROP TABLE x"}\n',
        b'{"action":"load_recoverable","offset":0}\n',
        b'{"action":"get","job_id":7,"job_id":8}\n',
        b'{"action":"get","job_id":7}',
    ]
    for payload in invalid:
        with pytest.raises(RuntimeLeaseBrokerError):
            _validate_request(payload)


def test_broker_round_trip_persist_state_get_and_recovery(tmp_path):
    store = _store(tmp_path)
    path = tmp_path / "lease-broker.sock"
    server = RuntimeLeaseBrokerServer(path, store=store, allowed_uid=os.getuid())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        assert server.ready.wait(2)
        client = RuntimeLeaseBrokerClient(path)
        record = RuntimeUnitRecord(1, "ai-chess-arena-runtime-1.service", "start_pending")
        client.persist(record)
        client.persist(record)  # ambiguous response retry is idempotent
        assert client.get(1) == record
        client.set_state(1, "active")
        assert client.get(1).state == "active"
        assert client.load_recoverable() == (
            RuntimeUnitRecord(1, "ai-chess-arena-runtime-1.service", "active"),
        )
        client.set_state(1, "stop_pending")
        client.set_state(1, "stopped")
        assert client.load_recoverable() == ()
        assert store.get(1).state == "stopped"
        assert (path.stat().st_mode & 0o777) == 0o600
    finally:
        server.shutdown()
        thread.join(3)
    assert not path.exists()


def test_broker_can_expose_group_write_socket_for_root_supervisor_peer(tmp_path):
    store = _store(tmp_path)
    path = tmp_path / "lease-broker.sock"
    server = RuntimeLeaseBrokerServer(
        path, store=store, allowed_uid=os.getuid(), socket_mode=0o660,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        assert server.ready.wait(2)
        info = path.stat()
        assert info.st_uid == os.getuid()
        assert info.st_gid == os.getgid()
        assert (info.st_mode & 0o777) == 0o660
        assert RuntimeLeaseBrokerClient(path).load_recoverable() == ()
    finally:
        server.shutdown()
        thread.join(3)
    assert not path.exists()


def test_broker_rejects_unauthorized_peer_before_dispatch(tmp_path):
    store = _store(tmp_path)
    path = tmp_path / "lease-broker.sock"
    calls = []
    original = store.get

    def tracked(job_id):
        calls.append(job_id)
        return original(job_id)

    store.get = tracked
    denied_uid = os.getuid() + 1
    server = RuntimeLeaseBrokerServer(path, store=store, allowed_uid=denied_uid)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        assert server.ready.wait(2)
        with pytest.raises(RuntimeLeaseBrokerError):
            RuntimeLeaseBrokerClient(path).get(1)
        assert calls == []
    finally:
        server.shutdown()
        thread.join(3)


def test_broker_bounds_recovery_response_without_truncation(tmp_path):
    store = _store(tmp_path, count=MAX_RECOVERABLE_RECORDS + 1)
    for job_id in range(1, MAX_RECOVERABLE_RECORDS + 2):
        store.persist(RuntimeUnitRecord(
            job_id, f"ai-chess-arena-runtime-{job_id}.service", "start_pending"
        ))
    path = tmp_path / "lease-broker.sock"
    server = RuntimeLeaseBrokerServer(path, store=store, allowed_uid=os.getuid())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        assert server.ready.wait(2)
        with pytest.raises(RuntimeLeaseBrokerError, match="RuntimeLeaseBrokerError"):
            RuntimeLeaseBrokerClient(path).load_recoverable()
    finally:
        server.shutdown()
        thread.join(3)


def test_broker_client_rejects_unavailable_socket(tmp_path):
    with pytest.raises(RuntimeLeaseBrokerError, match="unavailable"):
        RuntimeLeaseBrokerClient(tmp_path / "missing.sock").get(1)


def test_broker_is_ready_before_supervisor_recovery_callback(tmp_path):
    from types import SimpleNamespace
    from runtime_startup import recover_runtime_on_startup

    store = _store(tmp_path)
    store.persist(RuntimeUnitRecord(1, "ai-chess-arena-runtime-1.service", "start_pending"))
    path = tmp_path / "lease-broker.sock"
    server = RuntimeLeaseBrokerServer(path, store=store, allowed_uid=os.getuid())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    events = []
    try:
        assert server.ready.wait(2)
        client = RuntimeLeaseBrokerClient(path)

        def supervisor_recover():
            events.append(("supervisor", client.load_recoverable()))
            return {"count": 1}

        def executor_recover():
            events.append(("executor", None))
            return {"count": 0}

        result = recover_runtime_on_startup(
            deployment=SimpleNamespace(production_requested=True),
            supervisor_recover=supervisor_recover,
            executor_recover=executor_recover,
        )
        assert result["supervisor"]["count"] == 1
        assert [item[0] for item in events] == ["supervisor", "executor"]
        assert events[0][1] == (
            RuntimeUnitRecord(1, "ai-chess-arena-runtime-1.service", "start_pending"),
        )
    finally:
        server.shutdown()
        thread.join(3)


def test_concurrent_identical_persist_requests_commit_one_idempotent_lease(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    store = _store(tmp_path)
    record = RuntimeUnitRecord(1, "ai-chess-arena-runtime-1.service", "start_pending")
    barrier = threading.Barrier(2)

    def persist_once():
        barrier.wait(timeout=2)
        store.persist(record)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(persist_once) for _ in range(2)]
        for future in futures:
            future.result(timeout=5)

    assert store.get(1) == record
    assert store.load_recoverable() == (record,)


def test_cancel_transaction_winning_persist_race_prevents_lease_creation(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    store = _store(tmp_path)
    database = tmp_path / "arena.sqlite3"
    barrier = threading.Barrier(2)
    record = RuntimeUnitRecord(1, "ai-chess-arena-runtime-1.service", "start_pending")

    def cancel_first():
        with sqlite3.connect(database, timeout=5) as connection:
            connection.execute("BEGIN IMMEDIATE")
            barrier.wait(timeout=2)
            connection.execute(
                "UPDATE runtime_test_jobs SET cancel_requested=1 WHERE id=1"
            )

    def persist_after_lock():
        barrier.wait(timeout=2)
        try:
            store.persist(record)
        except Exception as exc:
            return type(exc).__name__
        return "persisted"

    with ThreadPoolExecutor(max_workers=2) as pool:
        cancel_future = pool.submit(cancel_first)
        persist_future = pool.submit(persist_after_lock)
        cancel_future.result(timeout=5)
        assert persist_future.result(timeout=5) == "RuntimeSupervisorError"

    assert store.get(1) is None


def test_broker_does_not_unlink_path_it_did_not_bind(tmp_path, monkeypatch):
    import socketserver
    path = tmp_path / "lease-broker.sock"
    server = RuntimeLeaseBrokerServer(path, store=_store(tmp_path), allowed_uid=os.getuid())

    def raced_bind(instance):
        Path(instance.server_address).write_text("foreign path", encoding="utf-8")
        raise OSError("simulated bind race")

    monkeypatch.setattr(socketserver.ThreadingUnixStreamServer, "server_bind", raced_bind)
    with pytest.raises(OSError, match="bind race"):
        server.serve_forever()
    assert path.read_text(encoding="utf-8") == "foreign path"


def test_graceful_broker_shutdown_drains_inflight_committed_request_and_restart_reads_lease(tmp_path):
    store = _store(tmp_path)
    committed = threading.Event()
    release_ack = threading.Event()
    original_persist = store.persist

    def persist_then_pause_ack(record):
        original_persist(record)
        committed.set()
        assert release_ack.wait(3)

    store.persist = persist_then_pause_ack
    path = tmp_path / "lease-broker.sock"
    server = RuntimeLeaseBrokerServer(path, store=store, allowed_uid=os.getuid())
    server_thread = threading.Thread(target=server.serve_forever)
    server_thread.start()
    request_result = []
    record = RuntimeUnitRecord(1, "ai-chess-arena-runtime-1.service", "start_pending")

    def send_persist():
        try:
            RuntimeLeaseBrokerClient(path).persist(record)
            request_result.append("acknowledged")
        except Exception as exc:
            request_result.append(type(exc).__name__)

    request_thread = threading.Thread(target=send_persist)
    try:
        assert server.ready.wait(2)
        request_thread.start()
        assert committed.wait(2)
        # Stop accepting new work while the in-flight handler is paused after
        # its SQLite commit but before sending the response.
        server.shutdown()
        server_thread.join(0.1)
        assert server_thread.is_alive()  # server_close drains non-daemon handlers
        assert store.get(1) == record
        release_ack.set()
        request_thread.join(2)
        server_thread.join(2)
        assert not request_thread.is_alive()
        assert not server_thread.is_alive()
        assert request_result == ["acknowledged"]
    finally:
        release_ack.set()
        if server.ready.is_set():
            server.shutdown()
        request_thread.join(2)
        server_thread.join(2)

    store.persist = original_persist
    restarted, restarted_thread, client = _start_test_broker(tmp_path, store)
    try:
        assert client.get(1) == record
        assert client.load_recoverable() == (record,)
    finally:
        restarted.shutdown()
        restarted_thread.join(3)
    assert not restarted_thread.is_alive()


def _start_test_broker(tmp_path, store):
    path = tmp_path / "lease-broker.sock"
    server = RuntimeLeaseBrokerServer(path, store=store, allowed_uid=os.getuid())
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    assert server.ready.wait(2)
    return server, thread, RuntimeLeaseBrokerClient(path)
