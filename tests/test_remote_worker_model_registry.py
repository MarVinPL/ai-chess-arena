"""Read-only Arena DB registry to Android GGUF source contract (Issue #295)."""
import json
import sqlite3

import pytest

from remote_worker_model_registry import (
    WorkerModelRegistryError, resolve_approved_worker_gguf,
)

SHA = "a" * 64
REV = "1" * 40
REPO = "approved/model-gguf"
NAME = "model.Q4_K_M.gguf"
URL = f"https://huggingface.co/{REPO}/resolve/{REV}/{NAME}"


def prepare(tmp_path):
    dbpath = tmp_path / "arena.db"
    metadata = dict(repo_id=REPO, revision=REV, filename=NAME,
                    source_path=NAME, sha256=SHA, size_bytes=4096,
                    download_url=URL)
    with sqlite3.connect(dbpath) as db:
        db.executescript("""
            CREATE TABLE league_models (id INTEGER PRIMARY KEY, name TEXT, active INTEGER);
            CREATE TABLE model_versions (
                id INTEGER PRIMARY KEY, model_id INTEGER, sha256 TEXT,
                size_bytes INTEGER, filename TEXT, metadata_json TEXT
            );
            CREATE TABLE model_admission_decisions (
                model_id INTEGER PRIMARY KEY, status TEXT, repo_id TEXT, revision TEXT
            );
        """)
        db.execute("INSERT INTO league_models VALUES (1,'approved/model-gguf',1)")
        db.execute("INSERT INTO model_versions VALUES (9,1,?,?,?,?)",
                   (SHA,4096,NAME,json.dumps(metadata)))
        db.execute("INSERT INTO model_admission_decisions VALUES (1,'admitted',?,?)",
                   (REPO,REV))
    return dbpath, metadata


def test_registry_returns_only_current_db_approved_pinned_file(tmp_path):
    dbpath, _ = prepare(tmp_path)
    assert resolve_approved_worker_gguf(dbpath,SHA) == {
        "model_id":1,"version_id":9,"sha256":SHA,
        "size_bytes":4096,"source_url":URL
    }


@pytest.mark.parametrize("query", [
    "UPDATE model_admission_decisions SET status='revoked' WHERE model_id=1",
    "UPDATE league_models SET active=0 WHERE id=1",
    "UPDATE model_versions SET sha256='bbbb' WHERE id=9",
    "INSERT INTO model_versions VALUES (10,1,'" + SHA + "',4096,'" + NAME + "','{}')",
    "DELETE FROM model_admission_decisions",
])
def test_registry_fails_closed_for_non_admitted_ambiguous_or_inactive(tmp_path,query):
    dbpath,_=prepare(tmp_path)
    with sqlite3.connect(dbpath) as db:db.execute(query)
    with pytest.raises(WorkerModelRegistryError):
        resolve_approved_worker_gguf(dbpath,SHA)


@pytest.mark.parametrize("field,value", [
    ("repo_id","another/repo"), ("revision","main"),
    ("filename","other.gguf"), ("sha256","b"*64),
    ("size_bytes",8192), ("download_url","https://evil.test/file.gguf"),
    ("source_path","subdir/model.gguf"),
])
def test_registry_rejects_tampered_metadata(tmp_path,field,value):
    dbpath,meta=prepare(tmp_path);meta[field]=value
    with sqlite3.connect(dbpath) as db:
        db.execute("UPDATE model_versions SET metadata_json=?",(json.dumps(meta),))
    with pytest.raises(WorkerModelRegistryError):
        resolve_approved_worker_gguf(dbpath,SHA)


def test_registry_is_read_only_and_does_not_create_missing_database(tmp_path):
    missing=tmp_path/"missing.db"
    with pytest.raises(WorkerModelRegistryError):
        resolve_approved_worker_gguf(missing,SHA)
    assert not missing.exists()


def test_registry_rejects_untrusted_selector(tmp_path):
    dbpath,_=prepare(tmp_path)
    for value in ("A"*64, "0", "a"*63, URL, None):
        with pytest.raises(WorkerModelRegistryError):
            resolve_approved_worker_gguf(dbpath,value)


def test_registry_admission_revision_must_match_model_revision(tmp_path):
    dbpath,_=prepare(tmp_path)
    with sqlite3.connect(dbpath) as db:
        db.execute("UPDATE model_admission_decisions SET revision=?",
                   ("2"*40,))
    with pytest.raises(WorkerModelRegistryError):
        resolve_approved_worker_gguf(dbpath,SHA)


def test_model_source_endpoint_requires_machine_token_and_existing_assignment():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    worker = (root / "worker_model_source_api.py").read_text(encoding="utf-8")
    router = (root / "worker_backend.py").read_text(encoding="utf-8")
    assert "WorkerTokenStore(database).authenticate(worker_id, token)" in worker
    assert "delivery.poll_assigned_job(worker_id)" in worker
    assert "assignment['job']['model_sha256']" not in worker or "model_sha256" in worker
    assert 'assignment["job"]["model_sha256"]' in worker
    assert "repeat != assignment" in worker
    assert "resolve_approved_worker_gguf(" in worker
    assert "install_allowed" not in worker
    assert "MODEL_SOURCE_ROUTE" in router
    assert "if path == MODEL_SOURCE_ROUTE:" in router
    assert "assign_from_arena(" not in worker
    assert "register_local_ready(" not in worker
