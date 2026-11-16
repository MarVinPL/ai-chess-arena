import sqlite3
import time

import pytest

import model_permissions
from runtime_supervisor_authority import (
    RuntimeAuthorityError,
    RuntimeJobAuthority,
    readonly_sqlite_factory,
)

REVISION = "a" * 40
SHA = "b" * 64
NOW = 1_893_456_000  # 2030-01-01T00:00:00Z


def seed_database(path):
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        model_permissions.ensure_schema(connection)
        connection.executescript("""
            CREATE TABLE runtime_execution_settings (
                id INTEGER PRIMARY KEY, enabled INTEGER NOT NULL
            );
            INSERT INTO runtime_execution_settings VALUES(1,1);
            CREATE TABLE runtime_test_batches (
                id INTEGER PRIMARY KEY, status TEXT NOT NULL, expires_at TEXT
            );
            INSERT INTO runtime_test_batches VALUES(4,'approved','2030-01-01T00:10:00Z');
            CREATE TABLE runtime_test_jobs (
                id INTEGER PRIMARY KEY, model_id INTEGER NOT NULL, repo_id TEXT NOT NULL,
                revision TEXT NOT NULL, artifact_key TEXT NOT NULL, artifact_sha256 TEXT NOT NULL,
                permission_request_id INTEGER NOT NULL, status TEXT NOT NULL,
                timeout_seconds INTEGER NOT NULL, cancel_requested INTEGER NOT NULL,
                batch_id INTEGER
            );
        """)
        connection.execute("""INSERT INTO model_permission_requests
            (id,model_id,repo_id,revision,artifact_sha256,scope,status,
             requested_by,requested_at,reason,snapshot_json)
            VALUES(9,3,'org/model',?,?,'TEST_ONLY','approved',
                   'operator','2029-12-01T00:00:00Z','test','{}')""", (REVISION, SHA))
        connection.execute("""INSERT INTO runtime_test_jobs
            VALUES(7,3,'org/model',?,?,?,9,'running',300,0,4)""",
            (REVISION, f"hf:org/model|{REVISION}|model.gguf", SHA))


def make_authority(path, *, opt_in=True, now=NOW):
    return RuntimeJobAuthority(
        connect_readonly=readonly_sqlite_factory(path),
        execution_opt_in=lambda: opt_in,
        clock=lambda: now,
    )


def test_resolves_job_from_read_only_database_and_returns_only_pinned_identity(tmp_path):
    db = tmp_path / "arena.sqlite3"
    seed_database(db)
    auth = make_authority(db).resolve_start(7)
    assert auth.job_id == 7
    assert auth.model_id == 3
    assert auth.repo_id == "org/model"
    assert auth.revision == REVISION
    assert auth.artifact_sha256 == SHA
    assert auth.permission_request_id == 9
    assert auth.batch_id == 4
    assert auth.timeout_seconds == 300
    assert auth.authorized_at == "2030-01-01T00:00:00Z"


def test_database_connection_factory_is_read_only(tmp_path):
    db = tmp_path / "arena.sqlite3"
    seed_database(db)
    connect = readonly_sqlite_factory(db)
    with connect() as connection:
        assert connection.execute("PRAGMA query_only").fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("UPDATE runtime_execution_settings SET enabled=0")


def test_fails_closed_without_environment_opt_in(tmp_path):
    db = tmp_path / "arena.sqlite3"
    seed_database(db)
    with pytest.raises(RuntimeAuthorityError, match="opt-in"):
        make_authority(db, opt_in=False).resolve_start(7)


@pytest.mark.parametrize("mutation,match", [
    ("UPDATE runtime_execution_settings SET enabled=0", "not explicitly enabled"),
    ("UPDATE runtime_test_jobs SET status='queued' WHERE id=7", "execution claim"),
    ("UPDATE runtime_test_jobs SET cancel_requested=1 WHERE id=7", "non-cancelled"),
    ("UPDATE runtime_test_jobs SET batch_id=NULL WHERE id=7", "not assigned"),
    ("UPDATE runtime_test_batches SET status='revoked' WHERE id=4", "missing or expired"),
    ("UPDATE model_permission_requests SET status='revoked' WHERE id=9", "not active"),
    ("UPDATE runtime_test_jobs SET artifact_sha256=? WHERE id=7", "does not match"),
])
def test_revalidates_every_gate(tmp_path, mutation, match):
    db = tmp_path / "arena.sqlite3"
    seed_database(db)
    with sqlite3.connect(db) as connection:
        if "?" in mutation:
            connection.execute(mutation, ("c" * 64,))
        else:
            connection.execute(mutation)
    with pytest.raises(RuntimeAuthorityError, match=match):
        make_authority(db).resolve_start(7)


def test_expired_approval_is_rejected(tmp_path):
    db = tmp_path / "arena.sqlite3"
    seed_database(db)
    with pytest.raises(RuntimeAuthorityError, match="expired"):
        make_authority(db, now=NOW + 601).resolve_start(7)


def test_grant_must_match_exact_model_repo_revision_hash_and_scope(tmp_path):
    db = tmp_path / "arena.sqlite3"
    seed_database(db)
    with sqlite3.connect(db) as connection:
        connection.execute("UPDATE model_permission_requests SET repo_id='other/model' WHERE id=9")
    with pytest.raises(RuntimeAuthorityError, match="does not match"):
        make_authority(db).resolve_start(7)


def test_rejects_writable_injected_connection(tmp_path):
    db = tmp_path / "arena.sqlite3"
    seed_database(db)
    def writable():
        connection = sqlite3.connect(db)
        connection.row_factory = sqlite3.Row
        return connection
    authority = RuntimeJobAuthority(connect_readonly=writable,
                                    execution_opt_in=lambda: True,
                                    clock=lambda: NOW)
    with pytest.raises(RuntimeAuthorityError, match="query-only"):
        authority.resolve_start(7)


def test_rejects_invalid_job_ids(tmp_path):
    db = tmp_path / "arena.sqlite3"
    seed_database(db)
    authority = make_authority(db)
    for job_id in (True, 0, -1, "7"):
        with pytest.raises(RuntimeAuthorityError):
            authority.resolve_start(job_id)
