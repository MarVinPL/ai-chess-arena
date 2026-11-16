import json
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
            CREATE TABLE hf_download_verified_artifacts (
                artifact_key TEXT PRIMARY KEY, path TEXT NOT NULL, size_bytes INTEGER NOT NULL,
                sha256 TEXT NOT NULL, verified_at TEXT NOT NULL, mtime_ns INTEGER NOT NULL
            );
            CREATE TABLE hf_runtime_preflight (
                artifact_key TEXT PRIMARY KEY, repo_id TEXT NOT NULL, path TEXT NOT NULL,
                size_bytes INTEGER NOT NULL, sha256 TEXT NOT NULL, status TEXT NOT NULL,
                checked_at TEXT NOT NULL,
                estimated_ram_bytes INTEGER, available_ram_bytes INTEGER
            );
            CREATE TABLE hf_candidate_reviews (
                catalog_id TEXT NOT NULL, revision TEXT NOT NULL, review_json TEXT NOT NULL,
                PRIMARY KEY(catalog_id,revision)
            );
        """)
        connection.execute("""INSERT INTO model_permission_requests
            (id,model_id,repo_id,revision,artifact_sha256,scope,status,
             requested_by,requested_at,reason,snapshot_json)
            VALUES(9,3,'org/model',?,?,'TEST_ONLY','approved',
                   'operator','2029-12-01T00:00:00Z','test','{}')""", (REVISION, SHA))
        artifact_key = f"hf:org/model|{REVISION}|model.gguf"
        connection.execute("""INSERT INTO runtime_test_jobs
            VALUES(7,3,'org/model',?,?,?,9,'running',300,0,4)""",
            (REVISION, artifact_key, SHA))
        connection.execute("""INSERT INTO hf_download_verified_artifacts
            VALUES(?,?,123,?,'2029-12-01T00:00:00Z',1)""",
            (artifact_key, "/quarantine/model.gguf", SHA))
        estimated = int(123 * 1.5) + 1536 * 1024**2
        connection.execute("""INSERT INTO hf_runtime_preflight
            VALUES(?,?,?,123,?,'ready_pending_approval','2030-01-01T00:00:00Z',?,?)""",
            (artifact_key, "org/model", "/quarantine/model.gguf", SHA, estimated, estimated + 1024))


def make_authority(path, *, opt_in=True, now=NOW):
    return RuntimeJobAuthority(
        connect_readonly=readonly_sqlite_factory(path),
        allowed_artifacts={f"hf:org/model|{REVISION}|model.gguf"},
        execution_opt_in=lambda: opt_in,
        resource_probe=lambda _path: (4 * 1024**3, 1024**3),
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
    assert auth.source_path == "/quarantine/model.gguf"
    assert auth.size_bytes == 123
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
    authority = RuntimeJobAuthority(
        connect_readonly=writable,
        allowed_artifacts={f"hf:org/model|{REVISION}|model.gguf"},
        execution_opt_in=lambda: True,
        resource_probe=lambda _path: (4 * 1024**3, 1024**3),
        clock=lambda: NOW,
    )
    with pytest.raises(RuntimeAuthorityError, match="query-only"):
        authority.resolve_start(7)


def test_rejects_invalid_job_ids(tmp_path):
    db = tmp_path / "arena.sqlite3"
    seed_database(db)
    authority = make_authority(db)
    for job_id in (True, 0, -1, "7"):
        with pytest.raises(RuntimeAuthorityError):
            authority.resolve_start(job_id)


def test_verified_registry_must_match_job_hash_and_artifact_key(tmp_path):
    db = tmp_path / "arena.sqlite3"
    seed_database(db)
    with sqlite3.connect(db) as connection:
        connection.execute(
            "UPDATE hf_download_verified_artifacts SET sha256=?",
            ("c" * 64,)
        )
    with pytest.raises(RuntimeAuthorityError, match="registry"):
        make_authority(db).resolve_start(7)


def test_missing_verified_registry_record_is_rejected(tmp_path):
    db = tmp_path / "arena.sqlite3"
    seed_database(db)
    with sqlite3.connect(db) as connection:
        connection.execute("DELETE FROM hf_download_verified_artifacts")
    with pytest.raises(RuntimeAuthorityError, match="registry"):
        make_authority(db).resolve_start(7)


def test_blocked_or_stale_preflight_prevents_authorization(tmp_path):
    db = tmp_path / "arena.sqlite3"
    seed_database(db)
    with sqlite3.connect(db) as connection:
        connection.execute("UPDATE hf_runtime_preflight SET status='skipped_blocked'")
    with pytest.raises(RuntimeAuthorityError, match="preflight"):
        make_authority(db).resolve_start(7)


def test_current_resource_probe_can_block_a_previously_ready_preflight(tmp_path):
    db = tmp_path / "arena.sqlite3"
    seed_database(db)
    authority = RuntimeJobAuthority(
        connect_readonly=readonly_sqlite_factory(db),
        allowed_artifacts={f"hf:org/model|{REVISION}|model.gguf"},
        execution_opt_in=lambda: True,
        resource_probe=lambda _path: (1, 1),
        clock=lambda: NOW,
    )
    with pytest.raises(RuntimeAuthorityError, match="RAM/disk"):
        authority.resolve_start(7)


def test_artifact_outside_privileged_allowlist_is_rejected(tmp_path):
    db = tmp_path / "arena.sqlite3"
    seed_database(db)
    authority = RuntimeJobAuthority(
        connect_readonly=readonly_sqlite_factory(db),
        allowed_artifacts={"different|artifact"},
        execution_opt_in=lambda: True,
        resource_probe=lambda _path: (4 * 1024**3, 1024**3),
        clock=lambda: NOW,
    )
    with pytest.raises(RuntimeAuthorityError, match="allowlist"):
        authority.resolve_start(7)


def test_review_required_candidate_needs_positive_manual_license_and_technical_review(tmp_path):
    db = tmp_path / "arena.sqlite3"
    seed_database(db)
    key = f"hf:org/model|{REVISION}|model.gguf"
    review = {
        "repo_id": "org/model", "revision": REVISION,
        "license_id": "Apache-2.0", "license_status": "verified_permissive", "license_evidence": "Verified license evidence from source.",
        "revision_confirmed": True, "technical_readiness": "ready",
        "technical_blockers": "",
    }
    with sqlite3.connect(db) as connection:
        connection.execute("INSERT INTO hf_candidate_reviews VALUES(?,?,?)",
                           ("hf:org/model", REVISION, json.dumps(review)))
    authority = RuntimeJobAuthority(
        connect_readonly=readonly_sqlite_factory(db),
        allowed_artifacts={key}, review_required_artifacts={key},
        execution_opt_in=lambda: True,
        resource_probe=lambda _path: (4 * 1024**3, 1024**3),
        clock=lambda: NOW,
    )
    assert authority.resolve_start(7).job_id == 7


def test_review_required_candidate_without_review_is_rejected(tmp_path):
    db = tmp_path / "arena.sqlite3"
    seed_database(db)
    key = f"hf:org/model|{REVISION}|model.gguf"
    authority = RuntimeJobAuthority(
        connect_readonly=readonly_sqlite_factory(db),
        allowed_artifacts={key}, review_required_artifacts={key},
        execution_opt_in=lambda: True,
        resource_probe=lambda _path: (4 * 1024**3, 1024**3),
        clock=lambda: NOW,
    )
    with pytest.raises(RuntimeAuthorityError, match="review"):
        authority.resolve_start(7)


def test_preflight_repo_binding_must_match_permission_and_job(tmp_path):
    db = tmp_path / "arena.sqlite3"
    seed_database(db)
    with sqlite3.connect(db) as connection:
        connection.execute("UPDATE hf_runtime_preflight SET repo_id='other/model'")
    with pytest.raises(RuntimeAuthorityError, match="preflight"):
        make_authority(db).resolve_start(7)


def test_declared_permissive_review_requires_apache_evidence(tmp_path):
    db = tmp_path / "arena.sqlite3"
    seed_database(db)
    key = f"hf:org/model|{REVISION}|model.gguf"
    review = {
        "repo_id": "org/model", "revision": REVISION,
        "license_id": "MIT", "license_status": "declared_permissive",
        "license_evidence": "A license is declared but not identified.",
        "revision_confirmed": True, "technical_readiness": "ready",
        "technical_blockers": "",
    }
    with sqlite3.connect(db) as connection:
        connection.execute("INSERT INTO hf_candidate_reviews VALUES(?,?,?)",
                           ("hf:org/model", REVISION, json.dumps(review)))
    authority = RuntimeJobAuthority(
        connect_readonly=readonly_sqlite_factory(db),
        allowed_artifacts={key}, review_required_artifacts={key},
        execution_opt_in=lambda: True,
        resource_probe=lambda _path: (4 * 1024**3, 1024**3),
        clock=lambda: NOW,
    )
    with pytest.raises(RuntimeAuthorityError, match="Apache-2.0"):
        authority.resolve_start(7)
