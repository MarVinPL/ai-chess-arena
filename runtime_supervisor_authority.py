"""Read-only authority gate for privileged runtime job handlers.

The IPC caller supplies only a job ID. This module resolves that ID against the
canonical Arena database and independently revalidates every execution gate.
It does not copy artifacts, build systemd units, or launch processes.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import model_permissions
from runtime_resource_policy import DISK_RESERVE_BYTES, required_host_available_bytes

_ARTIFACT_KEY = re.compile(r"^[^|\x00]{1,255}\|[0-9a-f]{40}\|[^/\\|\x00]{1,255}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PREFLIGHT_MAX_AGE_SECONDS = 15 * 60


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
    source_path: str
    size_bytes: int
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
                 allowed_artifacts: set[str] | frozenset[str],
                 review_required_artifacts: set[str] | frozenset[str] = frozenset(),
                 execution_opt_in: Callable[[], bool] | None = None,
                 resource_probe: Callable[[str], tuple[int, int]] | None = None,
                 clock: Callable[[], float] = time.time):
        if not callable(connect_readonly):
            raise TypeError("A read-only database connection factory is required.")
        if not isinstance(allowed_artifacts, (set, frozenset)) or not allowed_artifacts:
            raise TypeError("An explicit non-empty runtime artifact allowlist is required.")
        if not isinstance(review_required_artifacts, (set, frozenset)):
            raise TypeError("Review-required artifact policy must be a set.")
        if not review_required_artifacts.issubset(allowed_artifacts):
            raise ValueError("Review-required artifacts must also be runtime-allowlisted.")
        self._allowed_artifacts = frozenset(allowed_artifacts)
        self._review_required_artifacts = frozenset(review_required_artifacts)
        self._connect = connect_readonly
        self._execution_opt_in = execution_opt_in or (
            lambda: os.environ.get("ARENA_RUNTIME_EXECUTION_ENABLED") == "1"
        )
        self._resource_probe = resource_probe or self._host_resource_probe
        self._clock = clock

    @staticmethod
    def _host_resource_probe(source_path: str) -> tuple[int, int]:
        available = None
        with open("/proc/meminfo", "r", encoding="ascii") as stream:
            for line in stream:
                if line.startswith("MemAvailable:"):
                    available = int(line.split()[1]) * 1024
                    break
        if available is None:
            raise RuntimeAuthorityError("Current available RAM cannot be verified.")
        free_disk = shutil.disk_usage(source_path).free
        return available, free_disk

    def _validate_preflight(self, connection: sqlite3.Connection, *,
                            artifact_key: str, repo_id: str, source_path: str,
                            size_bytes: int, sha256: str, now_epoch: float,
                            now: str) -> None:
        row = connection.execute(
            "SELECT repo_id,path,size_bytes,sha256,status,checked_at,estimated_ram_bytes,"
            "available_ram_bytes FROM hf_runtime_preflight WHERE artifact_key=?",
            (artifact_key,)
        ).fetchone()
        if (not row or row["repo_id"] != repo_id
                or row["status"] != "ready_pending_approval"
                or row["path"] != source_path or row["size_bytes"] != size_bytes
                or row["sha256"] != sha256):
            raise RuntimeAuthorityError("Current runtime preflight is missing, blocked or mismatched.")
        fresh_after = time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime(now_epoch - _PREFLIGHT_MAX_AGE_SECONDS)
        )
        if (not isinstance(row["checked_at"], str)
                or not fresh_after <= row["checked_at"] <= now):
            raise RuntimeAuthorityError("Runtime preflight is stale or has an invalid timestamp.")
        try:
            estimated = required_host_available_bytes(size_bytes)
        except ValueError as exc:
            raise RuntimeAuthorityError("Artifact size exceeds runtime resource policy.") from exc
        if (type(row["estimated_ram_bytes"]) is not int
                or row["estimated_ram_bytes"] != estimated
                or type(row["available_ram_bytes"]) is not int
                or row["available_ram_bytes"] < estimated):
            raise RuntimeAuthorityError("Persisted RAM preflight does not satisfy the pinned artifact.")
        try:
            current_ram, current_disk = self._resource_probe(source_path)
        except Exception as exc:
            raise RuntimeAuthorityError("Current host resource preflight failed.") from exc
        if (type(current_ram) is not int or type(current_disk) is not int
                or current_ram < estimated or current_disk < DISK_RESERVE_BYTES):
            raise RuntimeAuthorityError("Current host RAM/disk reserve is insufficient.")

    @staticmethod
    def _validate_manual_review(connection: sqlite3.Connection, *,
                                catalog_id: str, repo_id: str, revision: str) -> None:
        row = connection.execute(
            "SELECT review_json FROM hf_candidate_reviews WHERE catalog_id=? AND revision=?",
            (catalog_id, revision)
        ).fetchone()
        if not row:
            raise RuntimeAuthorityError("Required manual candidate review is missing.")
        try:
            review = json.loads(row["review_json"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise RuntimeAuthorityError("Manual candidate review is malformed.") from exc
        if not isinstance(review, dict):
            raise RuntimeAuthorityError("Manual candidate review is malformed.")
        license_status = review.get("license_status")
        if not isinstance(license_status, str):
            raise RuntimeAuthorityError("Manual license review is malformed.")
        evidence = str(review.get("license_evidence") or "").lower()
        license_id = str(review.get("license_id") or "").strip()
        if not license_id or len(evidence.strip()) < 10:
            raise RuntimeAuthorityError("Manual license review lacks its identifier or evidence.")
        if license_status not in {"declared_permissive", "verified_permissive"}:
            raise RuntimeAuthorityError("Manual license review is not permissive.")
        if (license_status == "declared_permissive"
                and not any(token in evidence for token in ("apache-2.0", "apache 2.0"))):
            raise RuntimeAuthorityError("Declared permissive license lacks Apache-2.0 evidence.")
        if (review.get("repo_id") != repo_id or review.get("revision") != revision
                or review.get("revision_confirmed") is not True
                or review.get("technical_readiness") != "ready"
                or bool(review.get("technical_blockers"))):
            raise RuntimeAuthorityError("Manual technical/revision review is not ready.")

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
            if row["artifact_key"] not in self._allowed_artifacts:
                raise RuntimeAuthorityError("Artifact is not on the privileged runtime allowlist.")
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

            artifact = connection.execute(
                "SELECT path,size_bytes,sha256 FROM hf_download_verified_artifacts "
                "WHERE artifact_key=?", (row["artifact_key"],)
            ).fetchone()
            if (not artifact or not isinstance(artifact["path"], str)
                    or not artifact["path"].startswith("/")
                    or type(artifact["size_bytes"]) is not int or artifact["size_bytes"] < 1
                    or artifact["sha256"] != row["artifact_sha256"]):
                raise RuntimeAuthorityError("Verified quarantine registry does not match the pinned artifact.")

            self._validate_preflight(
                connection, artifact_key=row["artifact_key"], repo_id=row["repo_id"],
                source_path=artifact["path"], size_bytes=artifact["size_bytes"],
                sha256=artifact["sha256"],
                now_epoch=now_epoch, now=now,
            )
            if row["artifact_key"] in self._review_required_artifacts:
                self._validate_manual_review(
                    connection, catalog_id=catalog_id, repo_id=row["repo_id"],
                    revision=row["revision"],
                )

            timeout = row["timeout_seconds"]
            if type(timeout) is not int or not 1 <= timeout <= 1800:
                raise RuntimeAuthorityError("Runtime job timeout is outside policy.")

            return RuntimeJobAuthorization(
                job_id=job_id, model_id=int(row["model_id"]), repo_id=row["repo_id"],
                revision=row["revision"], artifact_key=row["artifact_key"],
                artifact_sha256=row["artifact_sha256"], source_path=artifact["path"],
                size_bytes=artifact["size_bytes"],
                permission_request_id=int(row["permission_request_id"]),
                batch_id=int(row["batch_id"]), timeout_seconds=timeout,
                authorized_at=now,
            )
