"""Trusted job-ID-only adapters for the privileged supervisor IPC.

All host-facing behavior is dependency-injected. Importing this module does
not create sockets, directories, accounts, systemd units, or model processes.
"""
from __future__ import annotations

import re
from pathlib import Path

from runtime_artifact_handoff import (
    ArtifactHandoffError,
    prepare_artifact_handoff,
    remove_artifact_handoff,
)
from runtime_supervisor import RuntimeSupervisor, SQLiteRuntimeUnitStore
from runtime_supervisor_authority import RuntimeJobAuthorization
from runtime_supervisor_ipc import RuntimeSupervisorIPCServer
from runtime_systemd import build_runtime_unit_spec
from runtime_resource_policy import model_memory_limit_bytes
from runtime_socket_handoff import (
    SocketHandoffSpec, create_handoff as create_socket_handoff,
    cleanup_handoff as cleanup_socket_handoff,
)

_JOB_DIR_RE = re.compile(r"^job-([1-9][0-9]*)$")

class RuntimeSupervisorHandlerError(RuntimeError):
    """Raised when a trusted job operation cannot complete safely."""


def _authorization_identity(auth: RuntimeJobAuthorization) -> tuple:
    return (
        auth.job_id, auth.model_id, auth.repo_id, auth.revision, auth.artifact_key,
        auth.artifact_sha256, auth.source_path, auth.size_bytes,
        auth.permission_request_id, auth.batch_id, auth.timeout_seconds,
    )


class RuntimeSupervisorHandlers:
    """Compose authority, immutable handoff, durable lease and exact-unit backend."""

    def __init__(self, *, authority, supervisor: RuntimeSupervisor,
                 lease_store: SQLiteRuntimeUnitStore, quarantine_root: str | Path,
                 handoff_root: str | Path, output_root: str | Path,
                 runtime_uid: int, runtime_gid: int, api_uid: int, api_gid: int,
                 supervisor_uid: int, supervisor_gid: int,
                 spec_builder: Callable | None = None):
        if not callable(getattr(authority, "resolve_start", None)):
            raise TypeError("Trusted job authority is required.")
        if not isinstance(supervisor, RuntimeSupervisor):
            raise TypeError("RuntimeSupervisor is required.")
        if not callable(getattr(lease_store, "get", None)):
            raise TypeError("A durable lease store with get() is required.")
        self._authority = authority
        self._supervisor = supervisor
        self._leases = lease_store
        self._quarantine_root = Path(quarantine_root)
        self._handoff_root = Path(handoff_root)
        self._output_root = Path(output_root)
        self._runtime_uid = runtime_uid
        self._runtime_gid = runtime_gid
        self._api_uid = api_uid
        self._api_gid = api_gid
        self._supervisor_uid = supervisor_uid
        self._supervisor_gid = supervisor_gid
        self._test_spec_builder = spec_builder is not None
        self._spec_builder = spec_builder or self._build_spec
        if not callable(self._spec_builder):
            raise TypeError("A trusted runtime spec builder is required.")
        for label, value in (
            ("runtime UID", runtime_uid), ("runtime GID", runtime_gid),
            ("API UID", api_uid), ("API GID", api_gid),
            ("supervisor UID", supervisor_uid), ("supervisor GID", supervisor_gid),
        ):
            if type(value) is not int or value < 0:
                raise ValueError(f"Invalid {label}.")
        if runtime_uid == 0 or runtime_gid == 0 or api_uid == 0 or api_gid == 0:
            raise ValueError("Runtime/API identities must be unprivileged.")
        if ((runtime_uid == api_uid or runtime_gid == api_gid)
                and not self._test_spec_builder):
            raise ValueError("Runtime/API identities must be distinct.")

    def _socket_spec(self, job_id: int) -> SocketHandoffSpec:
        return SocketHandoffSpec(
            job_id=job_id, runtime_uid=self._runtime_uid,
            api_gid=self._api_gid, output_root=self._output_root,
        )

    def _remove_artifact_handoff(self, job_id: int) -> bool:
        return remove_artifact_handoff(
            job_id=job_id, handoff_root=self._handoff_root,
            runtime_uid=self._runtime_uid, supervisor_gid=self._supervisor_gid,
            supervisor_uid=self._supervisor_uid,
        )

    def _remove_socket_handoff(self, job_id: int) -> bool:
        return cleanup_socket_handoff(
            self._socket_spec(job_id), supervisor_uid=self._supervisor_uid,
            supervisor_gid=self._supervisor_gid,
        )

    def _remove_job_handoffs(self, job_id: int) -> None:
        self._remove_socket_handoff(job_id)
        self._remove_artifact_handoff(job_id)

    def _create_socket_handoff(self, job_id: int) -> SocketHandoffSpec:
        spec = self._socket_spec(job_id)
        create_socket_handoff(
            spec, supervisor_uid=self._supervisor_uid,
            supervisor_gid=self._supervisor_gid,
        )
        return spec

    def _build_spec(self, authorization: RuntimeJobAuthorization, handoff,
                    socket_handoff: SocketHandoffSpec):
        # Host reserve is enforced separately by RuntimeJobAuthority; do not
        # assign that reserve to the runtime cgroup.
        memory_max = model_memory_limit_bytes(authorization.size_bytes)
        return build_runtime_unit_spec(
            job_id=authorization.job_id, runtime_user="arena-runtime",
            runtime_group="arena-runtime", artifact_path=handoff.path,
            artifact_mount_path=str(socket_handoff.directory / "model.gguf"),
            output_dir=str(socket_handoff.directory),
            api_uid=self._api_uid, api_gid=self._api_gid,
            memory_max_bytes=memory_max, memory_swap_max_bytes=0,
            cpu_quota_percent=100, tasks_max=8,
            runtime_seconds=authorization.timeout_seconds,
        )

    def start(self, job_id: int) -> dict:
        if type(job_id) is not int or job_id < 1:
            raise RuntimeSupervisorHandlerError("Invalid job ID.")
        if self._leases.get(job_id) is not None:
            raise RuntimeSupervisorHandlerError("A durable runtime lease already exists for this job.")

        handoff = None
        socket_handoff = None
        try:
            authorization = self._authority.resolve_start(job_id)
            if not isinstance(authorization, RuntimeJobAuthorization):
                raise RuntimeSupervisorHandlerError("Authority returned an invalid authorization record.")
            if authorization.job_id != job_id:
                raise RuntimeSupervisorHandlerError("Authority resolved a different job ID.")

            handoff = prepare_artifact_handoff(
                job_id=job_id, source_root=self._quarantine_root,
                source_path=authorization.source_path,
                expected_sha256=authorization.artifact_sha256,
                expected_size=authorization.size_bytes,
                handoff_root=self._handoff_root,
                runtime_uid=self._runtime_uid, supervisor_gid=self._supervisor_gid,
                supervisor_uid=self._supervisor_uid,
            )
            socket_handoff = self._create_socket_handoff(job_id)

            # Re-read all grants, batch state, preflight and host reserves after
            # copying. No unit may start on the strength of a stale first read.
            confirmed = self._authority.resolve_start(job_id)
            if (_authorization_identity(authorization)
                    != _authorization_identity(confirmed)):
                raise RuntimeSupervisorHandlerError("Job authorization changed during artifact handoff.")

            spec = self._spec_builder(authorization, handoff, socket_handoff)
            argv = (
                "/usr/local/lib/ai-chess-arena-runtime/runtime_worker",
                "--job", str(job_id),
            )
            def final_authorization_check():
                final = self._authority.resolve_start(job_id)
                if (_authorization_identity(authorization)
                        != _authorization_identity(final)):
                    raise RuntimeSupervisorHandlerError(
                        "Job authorization changed immediately before systemd start."
                    )

            unit_name = self._supervisor.start(
                spec, argv, before_start=final_authorization_check
            )
            return {
                "job_id": job_id,
                "unit_name": unit_name,
                "lease_state": "active",
                "unit_state": "start_requested",
            }
        except Exception as exc:
            # The supervisor persists the exact unit lease before asking
            # systemd to start. If a lease exists, the start may be ambiguous:
            # retain the handoff for exact-unit recovery.
            try:
                lease = self._leases.get(job_id)
            except Exception:
                lease = "unknown"
            if handoff is not None and (
                    lease is None or getattr(lease, "state", None) == "stopped"
            ):
                try:
                    self._remove_job_handoffs(job_id)
                except Exception as cleanup_exc:
                    raise RuntimeSupervisorHandlerError(
                        "Start failed and the unleased handoff could not be safely removed."
                    ) from cleanup_exc
            if isinstance(exc, RuntimeSupervisorHandlerError):
                raise
            raise RuntimeSupervisorHandlerError("Privileged runtime start was refused or uncertain.") from exc

    def stop(self, job_id: int) -> dict:
        if type(job_id) is not int or job_id < 1:
            raise RuntimeSupervisorHandlerError("Invalid job ID.")
        record = self._leases.get(job_id)
        if record is None:
            raise RuntimeSupervisorHandlerError("No durable runtime lease exists for this job.")
        if record.state in {
            "start_pending", "start_uncertain", "active", "stop_pending", "recovery_stop_pending"
        }:
            self._supervisor.stop(record)
        elif record.state != "stopped":
            raise RuntimeSupervisorHandlerError("Lease state is not eligible for safe stop.")
        # RuntimeSupervisor.stop returns only after the backend confirms the
        # exact unit cgroup is empty. A stopped lease is also safe to retry.
        self._remove_job_handoffs(job_id)
        return {"job_id": job_id, "unit_name": record.unit_name, "lease_state": "stopped"}

    def status(self, job_id: int) -> dict:
        if type(job_id) is not int or job_id < 1:
            raise RuntimeSupervisorHandlerError("Invalid job ID.")
        record = self._leases.get(job_id)
        if record is None:
            return {"job_id": job_id, "lease_exists": False, "lease_state": "absent"}
        # This reports durable lease state, not a live systemd ActiveState.
        return {
            "job_id": job_id, "lease_exists": True,
            "unit_name": record.unit_name, "lease_state": record.state,
        }

    def recover(self) -> dict:
        """Stop persisted exact units first, then remove only unleased/stopped handoffs."""
        result = self._supervisor.recover()
        cleaned = []
        failures = []
        for root, cleanup in (
            (self._handoff_root, self._remove_artifact_handoff),
            (self._output_root, self._remove_socket_handoff),
        ):
            try:
                entries = tuple(root.iterdir())
            except OSError as exc:
                raise RuntimeSupervisorHandlerError(
                    "Cannot inspect a pre-provisioned runtime handoff root."
                ) from exc
            for entry in entries:
                match = _JOB_DIR_RE.fullmatch(entry.name)
                if not match:
                    continue
                job_id = int(match.group(1))
                record = self._leases.get(job_id)
                if record is not None and record.state != "stopped":
                    continue
                try:
                    if cleanup(job_id) and job_id not in cleaned:
                        cleaned.append(job_id)
                except Exception:
                    failures.append(job_id)
        if failures:
            raise RuntimeSupervisorHandlerError(
                f"Safe handoff cleanup failed for job IDs: {','.join(map(str, sorted(set(failures))))}"
            )
        return {**result, "cleaned_handoffs": sorted(cleaned)}

    def make_ipc_server(self, socket_path: str | Path, *, allowed_uid: int,
                        socket_mode: int = 0o660) -> RuntimeSupervisorIPCServer:
        if type(allowed_uid) is not int or allowed_uid != self._api_uid:
            raise RuntimeSupervisorHandlerError("IPC peer UID must match the configured API identity.")
        return RuntimeSupervisorIPCServer(
            socket_path, allowed_uid=allowed_uid,
            start=self.start, stop=self.stop, status=self.status,
            socket_mode=socket_mode,
        )
