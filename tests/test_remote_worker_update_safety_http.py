"""HTTP integration tests for authenticated nonce-bound Worker update safety.

These tests use an ephemeral loopback server and temporary SQLite database,
never a deployed coordinator. The API must never authorize installation.
"""
import json
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

import remote_worker_tokens
import remote_worker_job_registry
import server


WORKER_ID = "worker_12345678"
NONCE = "00112233445566778899aabbccddeeff"


@pytest.fixture()
def endpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("ARENA_PHONE_WORKER_API_ENABLED", "1")
    db = tmp_path / "worker-safety.sqlite3"
    monkeypatch.setattr(server, "DB", db)
    from remote_worker_rate_policy import WorkerRatePolicy
    monkeypatch.setattr(server, "_worker_rate_policy", WorkerRatePolicy())
    token = remote_worker_tokens.WorkerTokenStore(db).issue(WORKER_ID, ttl_seconds=3600)
    remote_worker_job_registry.WorkerJobRegistry(db)
    http = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    try:
        def post(payload, *, route="update-safety", headers=None):
            data = json.dumps(payload).encode("utf-8")
            request = urllib.request.Request(
                f"http://127.0.0.1:{http.server_port}/api/remote-workers/{route}",
                data=data,
                headers={"Content-Type": "application/json", **(headers or {})},
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=4) as result:
                    return result.status, json.loads(result.read())
            except urllib.error.HTTPError as error:
                return error.code, json.loads(error.read())

        yield db, token, post
    finally:
        http.shutdown()
        http.server_close()
        thread.join(timeout=5)


def request_data(token, *, nonce=NONCE):
    return {"worker_id": WORKER_ID, "token": token, "nonce": nonce}


def test_no_job_row_is_unknown_and_never_grants_install(endpoint):
    _, token, post = endpoint
    status, response = post(request_data(token))
    assert status == 200
    assert response["state"] == "unknown"
    assert response["authenticated"] is True
    assert response["source"] == "coordinator"
    assert response["worker_id"] == WORKER_ID
    assert response["nonce"] == NONCE
    assert response["active_tasks"] is None
    assert abs(response["checked_at_unix_ms"] - int(time.time() * 1000)) < 5000
    assert response["install_allowed"] is False


def test_recent_real_terminal_state_only_can_report_idle(endpoint):
    db, token, post = endpoint
    now = time.time()
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO worker_job_safety VALUES (?,NULL,'idle_confirmed',NULL,?)",
            (WORKER_ID, now),
        )
    status, response = post(request_data(token))
    assert status == 200
    assert response["state"] == "idle_confirmed"
    assert response["active_tasks"] == 0
    assert response["install_allowed"] is False
    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE worker_job_safety SET updated_at=? WHERE worker_id=?",
            (now - 31, WORKER_ID),
        )
    status, response = post(request_data(token))
    assert status == 200
    assert response["state"] == "unknown"
    assert response["active_tasks"] is None
    assert response["install_allowed"] is False


def test_running_or_expired_job_is_never_idle(endpoint):
    db, token, post = endpoint
    now = time.time()
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO worker_job_safety VALUES (?,?,'running',?,?)",
            (WORKER_ID, "job_12345678", now + 60, now),
        )
    status, response = post(request_data(token))
    assert status == 200
    assert response["state"] == "running"
    assert response["active_tasks"] == 1
    assert response["install_allowed"] is False
    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE worker_job_safety SET deadline=? WHERE worker_id=?",
            (now - 1, WORKER_ID),
        )
    _, response = post(request_data(token))
    assert response["state"] == "unknown"
    assert response["active_tasks"] is None


def test_nonce_is_validated_and_not_reflected_on_auth_failure(endpoint):
    _, token, post = endpoint
    for wrong in ["bad", "A" * 32, "", "f" * 33, None]:
        status, response = post(request_data(token, nonce=wrong))
        assert status == 400
        assert "nonce" not in response
    status, response = post(request_data("incorrect-token"))
    assert status == 401
    assert "nonce" not in response
    assert "authenticated" not in response
    alternate = "a" * 32
    status, response = post(request_data(token, nonce=alternate))
    assert status == 200
    assert response["nonce"] == alternate
    assert response["install_allowed"] is False


def test_authenticated_reservation_blocks_assignments_until_explicit_release(endpoint):
    db, token, post = endpoint
    now = time.time()
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO worker_job_safety VALUES (?,NULL,'idle_confirmed',NULL,?)",
            (WORKER_ID, now),
        )
    expected_hold = "b" * 64
    status, reserved = post({
        **request_data(token), "hold_token": expected_hold
    }, route="update-reserve")
    assert status == 200
    assert reserved["authenticated"] is True
    assert reserved["nonce"] == NONCE
    assert reserved["install_allowed"] is False
    assert reserved["state"] == "reserved"
    hold_token = reserved["hold_token"]
    assert hold_token == expected_hold
    assert len(hold_token) == 64

    from remote_worker_jobs import DurableWorkerJobs
    jobs = DurableWorkerJobs(db)
    assert jobs.validate_update_hold(WORKER_ID, hold_token)
    with pytest.raises(ValueError, match="reserved"):
        jobs.assign(WORKER_ID, "job_12345678", timeout_seconds=30)

    # A second reservation fails and must not disturb the first.
    second_status, second = post({
        **request_data(token), "hold_token": "c" * 64
    }, route="update-reserve")
    assert second_status == 409
    assert "hold_token" not in second
    assert jobs.validate_update_hold(WORKER_ID, hold_token)

    # A forged token cannot unlock assignments.
    status, rejected = post({
        **request_data(token), "hold_token": "0" * 64
        if hold_token != "0" * 64 else "1" * 64
    }, route="update-release")
    assert status == 409
    assert rejected.get("released") is not True
    assert jobs.validate_update_hold(WORKER_ID, hold_token)

    status, released = post({
        **request_data(token), "hold_token": hold_token
    }, route="update-release")
    assert status == 200
    assert released["released"] is True
    assert released["state"] == "unknown"
    assert released["install_allowed"] is False
    assert not jobs.validate_update_hold(WORKER_ID, hold_token)
    assert jobs.state(WORKER_ID) == "unknown"
    with pytest.raises(ValueError):
        jobs.assign(WORKER_ID, "job_12345678", timeout_seconds=30)


def test_reservation_requires_machine_auth_and_fresh_idle(endpoint):
    db, token, post = endpoint
    status, reply = post({
        **request_data(token), "hold_token": "b" * 64
    }, route="update-reserve")
    assert status == 409
    assert "hold_token" not in reply
    status, reply = post({
        **request_data("bad-token"), "hold_token": "b" * 64
    }, route="update-reserve")
    assert status == 401
    assert "hold_token" not in reply
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO worker_job_safety VALUES (?,NULL,'idle_confirmed',NULL,?)",
            (WORKER_ID, time.time() - 40),
        )
    status, reply = post({
        **request_data(token), "hold_token": "b" * 64
    }, route="update-reserve")
    assert status == 409
    assert "hold_token" not in reply
    status, reply = post({
        **request_data(token, nonce="not-a-nonce"), "hold_token": "b" * 64
    }, route="update-reserve")
    assert status == 409
    assert "hold_token" not in reply


def test_reservation_rejects_absent_or_invalid_client_journal_secret(endpoint):
    db, token, post = endpoint
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO worker_job_safety VALUES (?,NULL,'idle_confirmed',NULL,?)",
            (WORKER_ID, time.time()),
        )
    for proposed in (None, "", "x" * 64, "0" * 63, "A" * 64):
        status, reply = post({
            **request_data(token), "hold_token": proposed
        }, route="update-reserve")
        assert status == 409
        assert "hold_token" not in reply
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT count(*) FROM worker_update_holds").fetchone()[0] == 0


def test_authenticated_hold_status_survives_expiry_without_releasing(endpoint):
    db, token, post = endpoint
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO worker_job_safety VALUES (?,NULL,'idle_confirmed',NULL,?)",
            (WORKER_ID, time.time()),
        )
    hold_token = "a" * 64
    status, reply = post({
        **request_data(token), "hold_token": hold_token
    }, route="update-reserve")
    assert status == 200
    assert reply["hold_token"] == hold_token
    request = {**request_data(token), "hold_token": hold_token}
    status, found = post(request, route="update-hold-status")
    assert status == 200
    assert found["state"] == "active"
    assert found["authenticated"] is True
    assert found["source"] == "coordinator"
    assert found["worker_id"] == WORKER_ID
    assert found["nonce"] == NONCE
    assert found["install_allowed"] is False
    assert "hold_token" not in found

    status, bad = post({
        **request_data(token), "hold_token": "b" * 64
    }, route="update-hold-status")
    assert status == 409
    assert "state" not in bad
    status, bad = post({
        **request_data("invalid-token"), "hold_token": hold_token
    }, route="update-hold-status")
    assert status == 401
    assert "state" not in bad

    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE worker_update_holds SET expires_at=? WHERE worker_id=?",
            (time.time() - 1, WORKER_ID)
        )
    status, expired = post(request, route="update-hold-status")
    assert status == 200
    assert expired["state"] == "expired"
    assert expired["install_allowed"] is False
    from remote_worker_jobs import DurableWorkerJobs
    with pytest.raises(ValueError, match="reserved"):
        DurableWorkerJobs(db).assign(WORKER_ID, "job_12345678", timeout_seconds=30)

    status, released = post(request, route="update-release")
    assert status == 200 and released["released"] is True
    status, absent = post(request, route="update-hold-status")
    assert status == 200 and absent["state"] == "absent"
    assert absent["install_allowed"] is False


def test_worker_poll_stop_instruction_is_authenticated_idempotent_and_read_only(endpoint):
    from remote_worker_update_pause import WorkerUpdatePause
    from remote_worker_jobs import DurableWorkerJobs
    db, machine_token, post = endpoint
    registry = DurableWorkerJobs(db)
    pause = WorkerUpdatePause(db)
    status, none = post(request_data(machine_token), route="update-pause-poll")
    assert status == 200
    assert none["instruction"] == {"action": "none"}
    assert none["install_allowed"] is False
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO worker_job_safety VALUES (?,?,'running',?,?)",
            (WORKER_ID, "job_12345678", time.time() + 300, time.time()),
        )
    request_id = pause.request(WORKER_ID, "job_12345678")
    for _ in range(3):
        status, received = post(request_data(machine_token), route="update-pause-poll")
        assert status == 200
        assert received["authenticated"] is True
        assert received["source"] == "coordinator"
        assert received["worker_id"] == WORKER_ID
        assert received["nonce"] == NONCE
        assert received["instruction"] == {
            "action": "stop_checkpoint",
            "job_id": "job_12345678", "request_id": request_id,
        }
        assert received["install_allowed"] is False
    assert registry.state(WORKER_ID) == "running"
    assert pause.pending(WORKER_ID)["phase"] == "requested"
    # A forged client claim is never accepted as checkpoint evidence.
    status, received = post({
        **request_data(machine_token), "worker_stopped": True,
        "checkpoint_verified": True, "resume_accepted_verified": True,
    }, route="update-pause-poll")
    assert status == 400
    assert "instruction" not in received
    assert pause.pending(WORKER_ID)["phase"] == "requested"
    with pytest.raises(ValueError, match="pause"):
        registry.reserve_update(WORKER_ID)

    pause.acknowledge(
        WORKER_ID, "job_12345678", request_id,
        worker_stopped=True, checkpoint_verified=True,
    )
    _, received = post(request_data(machine_token), route="update-pause-poll")
    assert received["instruction"] == {"action": "await_reconciliation"}
    assert registry.state(WORKER_ID) == "running"


def test_worker_poll_rejects_bad_auth_nonce_and_stale_job(endpoint):
    from remote_worker_update_pause import WorkerUpdatePause
    db, machine_token, post = endpoint
    status, response = post(request_data("wrong-machine-token"), route="update-pause-poll")
    assert status == 401 and "instruction" not in response
    for nonce in (None, "", "f" * 33, "F" * 32):
        status, response = post(
            request_data(machine_token, nonce=nonce), route="update-pause-poll"
        )
        assert status == 400 and "instruction" not in response
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO worker_job_safety VALUES (?,?,'running',?,?)",
            (WORKER_ID, "job_12345678", time.time() + 300, time.time()),
        )
    pause = WorkerUpdatePause(db)
    pause.request(WORKER_ID, "job_12345678")
    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE worker_job_safety SET deadline=? WHERE worker_id=?",
            (time.time() - 1, WORKER_ID)
        )
    status, response = post(request_data(machine_token), route="update-pause-poll")
    assert status == 200
    assert response["instruction"] == {"action": "blocked"}
    assert response["install_allowed"] is False
    with sqlite3.connect(db) as conn:
        assert conn.execute(
            "SELECT phase FROM worker_update_pause_requests WHERE worker_id=?",
            (WORKER_ID,)
        ).fetchone()[0] == "requested"


def test_update_safety_rejects_extra_fields_and_limits_authenticated_requests(endpoint):
    _, token, post = endpoint
    status, response = post({**request_data(token), "install_allowed": True})
    assert status == 400 and "nonce" not in response
    for _ in range(12):
        status, response = post(request_data(token))
        assert status == 200 and response["install_allowed"] is False
    status, response = post(request_data(token))
    assert status == 429 and "nonce" not in response


def journal_idle(db):
    from remote_worker_inference_delivery import WorkerInferenceDelivery
    WorkerInferenceDelivery(db)
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO worker_job_safety VALUES (?,NULL,'idle_confirmed',NULL,?)",
                     (WORKER_ID, time.time()))


def test_journal_evidence_complete_snapshot(endpoint):
    db, token, post = endpoint
    journal_idle(db)
    status, data = post(request_data(token), route="update-journal-evidence")
    assert status == 200
    assert data == {
        "contract_version": 1, "authenticated": True, "source": "coordinator",
        "worker_id": WORKER_ID, "nonce": NONCE,
        "checked_at_unix_ms": data["checked_at_unix_ms"],
        "state": "idle_confirmed", "active_tasks": 0,
        "hold_present": False, "pause_present": False, "checkpoint_present": False,
        "assignment_present": False, "release_recovery_present": False,
        "snapshot_only": True, "initialization_allowed": False, "install_allowed": False,
    }
    assert abs(data["checked_at_unix_ms"] - int(time.time()*1000)) < 5000
    assert token not in json.dumps(data)


@pytest.mark.parametrize("table,values,flag", [
    ("worker_update_holds", ("a"*64, 1, 2), "hold_present"),
    ("worker_update_pause_requests", ("job_12345678", "req_12345678", "requested", 1, None), "pause_present"),
    ("worker_update_resume", ("job_12345678", "req_12345678", "{}", "a"*64, 1), "checkpoint_present"),
    ("worker_inference_assignments", ("job_12345678", "{}", 1), "assignment_present"),
    ("worker_idle_update_recovery", (1,), "release_recovery_present"),
])
def test_journal_evidence_blocks_each_unresolved_record(endpoint, table, values, flag):
    db, token, post = endpoint
    journal_idle(db)
    with sqlite3.connect(db) as conn:
        conn.execute(f"INSERT INTO {table} VALUES ({','.join('?' for _ in range(len(values)+1))})",
                     (WORKER_ID, *values))
    status, data = post(request_data(token), route="update-journal-evidence")
    assert status == 200
    assert data[flag] is True and data["state"] == "unknown"
    assert data["active_tasks"] is None and data["initialization_allowed"] is False
    assert "a"*64 not in json.dumps(data)
    assert post(request_data(token), route="journal-init-acquire")[0] == 409


def test_journal_evidence_missing_schema_and_stale_idle(endpoint):
    db, token, post = endpoint
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO worker_job_safety VALUES (?,NULL,'idle_confirmed',NULL,?)",
                     (WORKER_ID, time.time()))
    status, data = post(request_data(token), route="update-journal-evidence")
    assert status == 200 and data["assignment_present"] is None and data["state"] == "unknown"
    from remote_worker_inference_delivery import WorkerInferenceDelivery
    WorkerInferenceDelivery(db)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE worker_job_safety SET updated_at=?", (time.time()-31,))
    status, data = post(request_data(token), route="update-journal-evidence")
    assert status == 200 and data["state"] == "unknown"


def test_journal_evidence_auth_nonce_and_replay_binding(endpoint):
    db, token, post = endpoint
    journal_idle(db)
    route = "update-journal-evidence"
    for payload in (request_data("x"*32), dict(request_data(token), worker_id="worker_87654321")):
        status, data = post(payload, route=route)
        assert status == 401 and "nonce" not in data
    assert post(request_data(token, nonce="bad"), route=route)[0] == 400
    assert post(dict(request_data(token), extra=True), route=route)[0] == 400
    first = post(request_data(token), route=route)[1]
    second = post(request_data(token, nonce="f"*32), route=route)[1]
    assert first["nonce"] != second["nonce"]
    remote_worker_tokens.WorkerTokenStore(db).revoke(WORKER_ID)
    assert post(request_data(token), route=route)[0] == 401


def test_journal_evidence_snapshot_is_consistent_during_concurrent_write(endpoint):
    db, token, _ = endpoint
    journal_idle(db)
    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
    registry = remote_worker_job_registry.WorkerJobRegistry(db)
    def admit(_):
        with sqlite3.connect(db) as writer:
            writer.execute("INSERT INTO worker_update_holds VALUES (?,?,?,?)",
                           (WORKER_ID, "a"*64, 1, 2))
    before = registry.journal_evidence(db, WORKER_ID, token, NONCE, admit=admit)
    after = registry.journal_evidence(db, WORKER_ID, token, NONCE, admit=lambda _: None)
    assert before["hold_present"] is False and before["state"] == "idle_confirmed"
    assert after["hold_present"] is True and after["state"] == "unknown"
    assert before["initialization_allowed"] is False


def test_journal_evidence_does_not_create_missing_schema(endpoint):
    db, token, post = endpoint
    journal_idle(db)
    with sqlite3.connect(db) as conn:
        conn.execute("DROP TABLE worker_update_holds")
    status, data = post(request_data(token), route="update-journal-evidence")
    assert status == 200 and data["hold_present"] is None and data["state"] == "unknown"
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT 1 FROM sqlite_schema WHERE name='worker_update_holds'").fetchone() is None


def test_journal_evidence_rejects_slow_snapshot_and_expired_token(endpoint):
    db, token, _ = endpoint
    journal_idle(db)
    registry = remote_worker_job_registry.WorkerJobRegistry
    now = time.time()
    times = iter((now, now, now+4))
    with pytest.raises(ValueError, match="deadline"):
        registry.journal_evidence(db, WORKER_ID, token, NONCE,
                                  admit=lambda _: None, clock=lambda: next(times))
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE worker_tokens SET expires_at=?", (now+1,))
    times = iter((now, now, now+2))
    assert registry.journal_evidence(db, WORKER_ID, token, NONCE,
                                    admit=lambda _: None, clock=lambda: next(times)) is None


def test_journal_evidence_rate_limit_and_running_state(endpoint):
    db, token, post = endpoint
    journal_idle(db)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE worker_job_safety SET state='running', job_id=?, deadline=?",
                     ("job_12345678", time.time()+60))
    for _ in range(12):
        status, data = post(request_data(token), route="update-journal-evidence")
        assert status == 200 and data["state"] == "unknown"
    assert post(request_data(token), route="update-journal-evidence")[0] == 429


def test_journal_init_token_that_expires_during_acquire_leaves_no_fence(endpoint):
    db, token, _ = endpoint
    journal_idle(db)
    now = time.time()
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE worker_tokens SET expires_at=?", (now + 1,))
    times = iter((now, now + 2))
    registry = remote_worker_job_registry.WorkerJobRegistry(db, clock=lambda: next(times))
    assert registry.acquire_journal_init(WORKER_ID, token, NONCE, admit=lambda _: None) is None
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT * FROM worker_journal_init_fences").fetchall() == []
        assert conn.execute("SELECT name FROM sqlite_schema WHERE type='trigger'").fetchall() == []


def test_journal_init_blocks_readiness_reservation_and_restart(endpoint):
    from remote_worker_inference_delivery import WorkerInferenceDelivery
    from remote_worker_job_binding import BoundWorkerResults
    from remote_worker_readiness import WorkerNotReady, assert_worker_eligible

    db, token, post = endpoint
    delivery = WorkerInferenceDelivery(db)
    model = "a"*64
    assert delivery.register_local_ready(WORKER_ID, model) == "ready"
    assert delivery.find_ready_worker(model) == WORKER_ID
    assert post(request_data(token), route="journal-init-acquire")[0] == 200

    registry = remote_worker_job_registry.WorkerJobRegistry(db)
    assert delivery.find_ready_worker(model) is None
    assert delivery.poll_assigned_job(WORKER_ID) == {"state": "blocked"}
    assert post(request_data(token))[1]["state"] == "unknown"
    assert post(request_data(token), route="update-journal-evidence")[1]["state"] == "unknown"
    assert post(dict(request_data(token), model_sha256=model), route="ready")[0] == 400
    assert post(dict(request_data(token), hold_token="b"*64), route="update-reserve")[0] == 409
    with pytest.raises(WorkerNotReady, match="initialization fenced"):
        assert_worker_eligible(db, WORKER_ID, liveness_verified=True)
    bound = BoundWorkerResults(db)
    with pytest.raises(ValueError, match="initialization fenced"):
        bound.assign_bound(WORKER_ID, "job_12345678", tournament_id=1, pairing_id=1,
                           expected_sha256="a"*64, timeout_seconds=30)
    registry.recover_after_restart()
    restarted = remote_worker_job_registry.WorkerJobRegistry(db, clock=lambda: time.time() + 86400)
    assert restarted.state(WORKER_ID) == "unknown"
    with pytest.raises(ValueError, match="initialization fenced"):
        restarted.record_assignment(WORKER_ID, "job_12345678", deadline=restarted.clock() + 30)
    with pytest.raises(ValueError, match="initialization fenced"):
        bound.confirm_idle_after_restart(WORKER_ID, operator_confirmed=True, worker_reconnected=True)
    assert post(request_data(token), route="journal-init-acquire")[0] == 409


def test_journal_init_blocks_direct_sql_for_the_fenced_worker(endpoint):
    db, token, post = endpoint
    journal_idle(db)
    assert post(request_data(token), route="journal-init-acquire")[0] == 200
    with sqlite3.connect(db) as conn:
        for statement, arguments in (
            ("UPDATE worker_job_safety SET updated_at=? WHERE worker_id=?", (time.time(), WORKER_ID)),
            ("INSERT OR REPLACE INTO worker_job_safety VALUES (?,?,'running',?,?)",
             (WORKER_ID, "job_12345678", time.time() + 30, time.time())),
            ("INSERT INTO worker_inference_assignments VALUES (?,?,?,?)",
             (WORKER_ID, "job_12345678", "{}", 1)),
            ("INSERT INTO worker_inference_capabilities VALUES (?,?,?,?)", (WORKER_ID, "a"*64, 1, 2)),
            ("INSERT INTO worker_update_holds VALUES (?,?,?,?)", (WORKER_ID, "a"*64, 1, 2)),
            ("INSERT INTO worker_update_pause_requests VALUES (?,?,?,?,?,?)",
             (WORKER_ID, "job_12345678", "req_12345678", "requested", 1, None)),
            ("INSERT INTO worker_update_resume VALUES (?,?,?,?,?,?)",
             (WORKER_ID, "job_12345678", "req_12345678", "{}", "a"*64, 1)),
            ("INSERT INTO worker_idle_update_recovery VALUES (?,?)", (WORKER_ID, 1)),
        ):
            with pytest.raises(sqlite3.IntegrityError, match="initialization fenced"):
                conn.execute(statement, arguments)
        other = "worker_87654321"
        conn.execute("INSERT INTO worker_idle_update_recovery VALUES (?,?)", (other, 1))
        with pytest.raises(sqlite3.IntegrityError, match="initialization fenced"):
            conn.execute("UPDATE worker_idle_update_recovery SET worker_id=? WHERE worker_id=?",
                         (WORKER_ID, other))


def test_journal_init_blocks_unapplied_bound_result(endpoint):
    from remote_worker_result_journal import WorkerResultJournal

    db, token, post = endpoint
    journal_idle(db)
    WorkerResultJournal(db)
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO worker_result_journal VALUES (?,?,?,?,?)",
                     ("job_12345678", WORKER_ID, "{}", "pending_application", time.time()))
    status, data = post(request_data(token), route="journal-init-acquire")
    assert status == 409 and set(data) == {"error"}
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT 1 FROM worker_journal_init_fences").fetchone() is None


def test_journal_init_http_acquire_requires_current_token_and_valid_nonce(endpoint):
    db, token, post = endpoint
    route = "journal-init-acquire"
    assert post(request_data(token), route=route)[0] == 503
    journal_idle(db)
    for payload, expected in (
        (request_data("x"*32), 401),
        (dict(request_data(token), worker_id="worker_87654321"), 401),
        (request_data(token, nonce="bad"), 400),
        (dict(request_data(token), install_allowed=True), 400),
    ):
        status, data = post(payload, route=route)
        assert status == expected and set(data) == {"error"}
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE worker_job_safety SET updated_at=?", (time.time() - 31,))
    assert post(request_data(token), route=route)[0] == 409
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE worker_job_safety SET updated_at=?", (time.time(),))
        conn.execute("UPDATE worker_tokens SET expires_at=?", (time.time() - 1,))
    assert post(request_data(token), route=route)[0] == 401

    token = remote_worker_tokens.WorkerTokenStore(db).issue(WORKER_ID)
    status, data = post(request_data(token), route=route)
    assert status == 200
    assert data == {
        "contract_version": 1, "authenticated": True, "source": "coordinator",
        "worker_id": WORKER_ID, "nonce": NONCE, "fenced": True,
        "checked_at_unix_ms": data["checked_at_unix_ms"], "install_allowed": False,
    }
    assert abs(data["checked_at_unix_ms"] - int(time.time() * 1000)) < 5000
    assert token not in json.dumps(data)
    for nonce in (NONCE, "f"*32):
        status, reply = post(request_data(token, nonce=nonce), route=route)
        assert status == 409 and set(reply) == {"error"}

    replacement = remote_worker_tokens.WorkerTokenStore(db).issue(WORKER_ID)
    assert post(request_data(token), route=route)[0] == 401
    assert post(request_data(replacement), route=route)[0] == 409
    with sqlite3.connect(db) as conn:
        fence = conn.execute("SELECT nonce,token_digest FROM worker_journal_init_fences").fetchone()
    assert fence == (NONCE, remote_worker_tokens.WorkerTokenStore._digest(token))



def test_journal_init_completion_conflicts_and_idempotent_response(endpoint):
    from hashlib import sha256

    db, token, post = endpoint
    journal_idle(db)
    assert post(request_data(token), route="journal-init-acquire", headers={"Host": "arena.example"})[0] == 200
    origin = "https://arena.example"
    marker = sha256(b"\0".join((origin.encode(), WORKER_ID.encode(),
                                sha256(token.encode()).digest(), NONCE.encode()))).hexdigest()
    payload = dict(request_data(token), pair_marker=marker, origin=origin)
    def complete(body, host="arena.example"):
        return post(body, route="journal-init-complete", headers={"Host": host})

    assert complete(payload, host="other.example")[0] == 400
    other = "https://other.example"
    other_marker = sha256(b"\0".join((other.encode(), WORKER_ID.encode(),
                                      sha256(token.encode()).digest(), NONCE.encode()))).hexdigest()
    assert complete(dict(payload, origin=other, pair_marker=other_marker), host="other.example")[0] == 409
    assert complete(dict(payload, pair_marker="a"*64))[0] == 409
    assert complete(dict(payload, nonce="b"*32))[0] == 409
    assert complete(dict(payload, token="c"*43))[0] == 401
    registry = remote_worker_job_registry.WorkerJobRegistry(db)
    with pytest.raises(ValueError, match="initialization fenced"):
        registry.record_assignment(WORKER_ID, "job_12345678", deadline=time.time() + 30)

    status, first = complete(payload)
    assert status == 200 and first == {
        "contract_version": 1, "authenticated": True, "source": "coordinator",
        "worker_id": WORKER_ID, "nonce": NONCE, "completed": True,
        "state": "unknown", "install_allowed": False,
    }
    registry.recover_after_restart()
    assert complete(payload) == (200, first)
    assert post(request_data(token), route="journal-init-acquire")[0] == 409
    with pytest.raises(ValueError, match="unresolved job state"):
        registry.record_assignment(WORKER_ID, "job_12345678", deadline=time.time() + 30)
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT nonce,pair_marker FROM worker_journal_init_completions").fetchall() == [(NONCE, marker)]
        assert conn.execute("SELECT 1 FROM worker_journal_init_fences").fetchone() is None


def test_journal_init_completion_under_rotated_token_keeps_fence(endpoint):
    from hashlib import sha256

    db, token, post = endpoint
    journal_idle(db)
    assert post(request_data(token), route="journal-init-acquire", headers={"Host": "arena.example"})[0] == 200
    rotated = remote_worker_tokens.WorkerTokenStore(db).issue(WORKER_ID)
    origin = "https://arena.example"
    marker = sha256(b"\0".join((origin.encode(), WORKER_ID.encode(),
                                sha256(rotated.encode()).digest(), NONCE.encode()))).hexdigest()
    payload = dict(request_data(rotated), pair_marker=marker, origin=origin)
    assert post(payload, route="journal-init-complete", headers={"Host": "arena.example"})[0] == 409
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT nonce FROM worker_journal_init_fences").fetchall() == [(NONCE,)]
        assert conn.execute("SELECT 1 FROM worker_journal_init_completions").fetchone() is None



def test_journal_init_completion_storage_failure_rolls_back(endpoint):
    from hashlib import sha256

    db, token, post = endpoint
    journal_idle(db)
    assert post(request_data(token), route="journal-init-acquire", headers={"Host": "arena.example"})[0] == 200
    origin = "https://arena.example"
    marker = sha256(b"\0".join((origin.encode(), WORKER_ID.encode(),
                                sha256(token.encode()).digest(), NONCE.encode()))).hexdigest()
    payload = dict(request_data(token), pair_marker=marker, origin=origin)
    with sqlite3.connect(db) as conn:
        conn.execute("""
            CREATE TRIGGER fail_test_fence_delete BEFORE DELETE ON worker_journal_init_fences BEGIN
                SELECT RAISE(ABORT, 'simulated storage failure');
            END
        """)
    assert post(payload, route="journal-init-complete", headers={"Host": "arena.example"})[0] == 503
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT nonce FROM worker_journal_init_fences").fetchall() == [(NONCE,)]
        assert conn.execute("SELECT 1 FROM worker_journal_init_completions").fetchone() is None
        assert conn.execute("SELECT state FROM worker_job_safety").fetchone() == ("idle_confirmed",)
        conn.execute("DROP TRIGGER fail_test_fence_delete")
    assert post(payload, route="journal-init-complete", headers={"Host": "arena.example"})[0] == 200
