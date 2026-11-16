"""Read-only authority gate for privileged runtime job handlers.

The IPC caller supplies only a job ID. This module resolves that ID against the
canonical Arena database and independently revalidates every execution gate.
It does not copy artifacts, build systemd units, or launch processes.
"""
from __future__ import annotations

import os
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import model_permissions

_ARTIFACT_KEY = re.compile(r"^[^|\x00]{1,255}\|[0-9a-f]{40}\|[^/\\|\x00]{1,255}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class RuntimeAuthorityError(PermissionError):
    """A job failed one or more independent runtime authorization gates."""


@dataclass(frozen=True)
class RuntimeJobAuthorization:
    job_id: int
    model_id: int
    repo_id: str
    revision: str
    artifact_key: str
    artifact_sha256: str
    permission_request_id: int
    batch_id: int
    timeout_seconds: int
    authorized_at: str


def readonly_sqlite_factory(database_path: str | Path) -> Callable[[], sqlite3.Connection]:
    """Return connections that cannot write or create a missing database."""
    uri = Path(database_path).resolve(strict=True).as_uri() + "?mode=ro"
    def connect() -> sqlite3.Connection:
        connection = sqlite3.connect(uri, uri=True, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA trusted_schema=OFF")
        return connection
    return connect


class RuntimeJobAuthority:
    """Resolve start requests from trusted DB state; never trust IPC payload data."""

    def __init__(self, *, connect_readonly: Callable[[], sqlite3.Connection],
                 execution_opt_in: Callable[[], bool] | None = None,
                 clock: Callable[[], float] = time.time):
        if not callable(connect_readonly):
            raise TypeError("A read-only database connection factory is required.")
        self._connect = connect_readonly
        self._execution_opt_in = execution_opt_in or (
            lambda: os.environ.get("ARENA_RUNTIME_EXECUTION_ENABLED") == "1"
        )
        self._clock = clock

    @staticmethod
    def _job_id(job_id: int) -> int:
        if type(job_id) is not int or job_id < 1:
            raise RuntimeAuthorityError("Invalid runtime job ID.")
        return job_id

    def resolve_start(self, job_id: int) -> RuntimeJobAuthorization:
        """Authorize one queued job from current DB state using a read-only snapshot."""
        job_id = self._job_id(job_id)
        if not self._execution_opt_in():
            raise RuntimeAuthorityError("Independent runtime opt-in is disabled.")

        now_epoch = self._clock()
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now_epoch))
        with self._connect() as connection:
            query_only = connection.execute("PRAGMA query_only").fetchone()
            if not query_only or int(query_only[0]) != 1:
                raise RuntimeAuthorityError("Supervisor DB connection is not query-only.")

            connection.execute("BEGIN")
            settings = connection.execute(
                "SELECT enabled FROM runtime_execution_settings WHERE id=1"
            ).fetchone()
            if not settings or int(settings["enabled"]) != 1:
                raise RuntimeAuthorityError("Runtime is not explicitly enabled in the database.")

            row = connection.execute(
                "SELECT id,model_id,repo_id,revision,artifact_key,artifact_sha256,"
                "permission_request_id,status,timeout_seconds,cancel_requested,batch_id "
                "FROM runtime_test_jobs WHERE id=?", (job_id,)
            ).fetchone()
            if not row:
                raise RuntimeAuthorityError("Runtime job does not exist.")
            if row["status"] != "running" or int(row["cancel_requested"]):
                raise RuntimeAuthorityError("Runtime job has no active, non-cancelled execution claim.")
            if row["batch_id"] is None:
                raise RuntimeAuthorityError("Runtime job is not assigned to an approved batch.")

            batch = connection.execute(
                "SELECT status,expires_at FROM runtime_test_batches WHERE id=?",
                (row["batch_id"],)
            ).fetchone()
            if (not batch or batch["status"] not in {"approved", "running"}
                    or not isinstance(batch["expires_at"], str)
                    or batch["expires_at"] <= now):
                raise RuntimeAuthorityError("Runtime batch approval is missing or expired.")

            grant = connection.execute(
                "SELECT model_id,repo_id,revision,artifact_sha256,scope,status "
                "FROM model_permission_requests WHERE id=?",
                (row["permission_request_id"],)
            ).fetchone()
            if not grant or grant["status"] != "approved" or grant["scope"] != "TEST_ONLY":
                raise RuntimeAuthorityError("Exact TEST_ONLY permission is not active.")
            expected = (row["model_id"], row["repo_id"], row["revision"], row["artifact_sha256"])
            actual = (grant["model_id"], grant["repo_id"], grant["revision"], grant["artifact_sha256"])
            if actual != expected:
                raise RuntimeAuthorityError("TEST_ONLY permission does not match the pinned job.")

            if (not isinstance(row["repo_id"], str)
                    or not isinstance(row["revision"], str)
                    or not isinstance(row["artifact_sha256"], str)
                    or not _SHA256.fullmatch(row["artifact_sha256"])
                    or not isinstance(row["artifact_key"], str)
                    or not _ARTIFACT_KEY.fullmatch(row["artifact_key"])):
                raise RuntimeAuthorityError("Pinned artifact identity is malformed.")
            try:
                catalog_id, key_revision, _filename = row["artifact_key"].split("|", 2)
                if not catalog_id or key_revision != row["revision"]:
                    raise ValueError("artifact key revision mismatch")
                model_permissions._validate_identity(
                    int(row["model_id"]), row["repo_id"], row["revision"], row["artifact_sha256"]
                )
                if not model_permissions.has_permission(
                    connection, model_id=int(row["model_id"]), repo_id=row["repo_id"],
                    revision=row["revision"], artifact_sha256=row["artifact_sha256"],
                    scope="TEST_ONLY"
                ):
                    raise ValueError("active TEST_ONLY scope missing")
            except (TypeError, ValueError) as exc:
                raise RuntimeAuthorityError("Pinned TEST_ONLY identity validation failed.") from exc

            timeout = row["timeout_seconds"]
            if type(timeout) is not int or not 1 <= timeout <= 1800:
                raise RuntimeAuthorityError("Runtime job timeout is outside policy.")

            return RuntimeJobAuthorization(
                job_id=job_id, model_id=int(row["model_id"]), repo_id=row["repo_id"],
                revision=row["revision"], artifact_key=row["artifact_key"],
                artifact_sha256=row["artifact_sha256"],
                permission_request_id=int(row["permission_request_id"]),
                batch_id=int(row["batch_id"]), timeout_seconds=timeout,
                authorized_at=now,
            )
