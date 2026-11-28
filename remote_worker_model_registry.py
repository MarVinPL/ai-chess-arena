"""Read-only, fail-closed GGUF metadata resolver for an Arena Worker.

The SQLite admission decision is the *only* authority to approve a model.
A worker job SHA is merely a selector, never a URL or authorization.
This module does not expose an HTTP endpoint, download weights or mutate DB.
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from urllib.parse import urlsplit

_SHA = re.compile(r"^[0-9a-f]{64}$")
_REV = re.compile(r"^[0-9a-fA-F]{40}$")
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,180}\.gguf$")
_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_MAX_BYTES = 16 * 1024**3


class WorkerModelRegistryError(ValueError):
    pass


def resolve_approved_worker_gguf(db_path: str | Path, assigned_sha256: str) -> dict:
    """Require one version with an explicit *current* DB admission decision.

    Caller must separately authenticate the worker and verify the current
    assignment, lease, STOP/ACK and digest before downloading or running.
    The returned dict contains public model bytes metadata, no capabilities.
    """
    if not isinstance(assigned_sha256, str) or not _SHA.fullmatch(assigned_sha256):
        raise WorkerModelRegistryError("Nieprawidłowy SHA-256 przydziału")
    uri = f"file:{Path(db_path).resolve().as_posix()}?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True, timeout=5) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                """SELECT v.id AS version_id,v.model_id,v.sha256,v.size_bytes,
                          v.filename,v.metadata_json,m.name AS model_name,
                          a.repo_id AS admitted_repo,a.revision AS admitted_revision,
                          a.status AS admission_status
                   FROM model_versions v
                   JOIN league_models m ON m.id=v.model_id
                   JOIN model_admission_decisions a ON a.model_id=m.id
                   WHERE v.sha256=? AND m.active=1 AND a.status='admitted'
                   LIMIT 2""", (assigned_sha256,)
            ).fetchall()
    except (sqlite3.Error, OSError) as exc:
        raise WorkerModelRegistryError("Rejestr modeli Areny jest niedostępny") from exc

    if len(rows) != 1:
        raise WorkerModelRegistryError("Brak jednoznacznej dopuszczonej wersji GGUF")
    row = rows[0]
    try:
        metadata = json.loads(row["metadata_json"])
        repo = row["admitted_repo"]
        revision = row["admitted_revision"]
        size = row["size_bytes"]
        filename = row["filename"]
        if not isinstance(metadata, dict):
            raise ValueError("Invalid metadata")
        if not isinstance(repo, str) or not _REPO.fullmatch(repo):
            raise ValueError("Invalid repo")
        if not isinstance(revision, str) or not _REV.fullmatch(revision):
            raise ValueError("Invalid revision")
        if not isinstance(filename, str) or not _NAME.fullmatch(filename):
            raise ValueError("Invalid GGUF filename")
        if type(size) is not int or not 4 <= size <= _MAX_BYTES:
            raise ValueError("Invalid size")
        if (metadata.get("repo_id") != repo
                or metadata.get("revision") != revision
                or metadata.get("sha256") != assigned_sha256
                or metadata.get("size_bytes") != size
                or metadata.get("filename") != filename
                or metadata.get("source_path") != filename):
            raise ValueError("Registry and approved metadata differ")
        url = f"https://huggingface.co/{repo}/resolve/{revision}/{filename}"
        if metadata.get("download_url") != url:
            raise ValueError("Source URL mismatch")
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.netloc != "huggingface.co":
            raise ValueError("Untrusted source")
    except (TypeError, ValueError, KeyError) as exc:
        raise WorkerModelRegistryError("Niezgodne metadane zatwierdzonego GGUF") from exc
    return {
        "model_id": row["model_id"],
        "version_id": row["version_id"],
        "sha256": assigned_sha256,
        "size_bytes": size,
        "source_url": url,
    }
