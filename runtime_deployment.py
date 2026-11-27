"""Declarative deployment profile for the Issue #157 runtime integration.

This module is intentionally side-effect free: resolving a profile never opens
SQLite, touches systemd, creates files, or launches a process. It gives staging
and production one stable configuration contract while production wiring is
still being completed and independently reviewed.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import PurePosixPath
from typing import Mapping


class RuntimeDeploymentConfigError(ValueError):
    """Raised when a deployment profile is incomplete or unsafe."""


@dataclass(frozen=True)
class RuntimeDeploymentConfig:
    environment: str
    backend: str
    execution_enabled: bool
    artifact_root: str
    output_root: str
    worker_bundle: str
    supervisor_socket: str | None
    lease_broker_socket: str | None
    unit_namespace: str = "production"

    @property
    def supervisor_requested(self) -> bool:
        return self.environment in {"production", "test-recovery"}

    @property
    def production_requested(self) -> bool:
        return self.environment == "production"


def _absolute_path(value: str, key: str) -> str:
    if not isinstance(value, str) or not value.startswith("/") or "\x00" in value:
        raise RuntimeDeploymentConfigError(f"{key} musi być bezwzględną ścieżką.")
    path = PurePosixPath(value)
    if ".." in path.parts:
        raise RuntimeDeploymentConfigError(f"{key} nie może zawierać '..'.")
    return str(path)


def resolve_runtime_deployment(environ: Mapping[str, str] | None = None) -> RuntimeDeploymentConfig:
    """Resolve disabled/staging/test-recovery/production without enabling execution by itself.

    Default is disabled. Staging is deliberately inert. Production requires an
    explicit acknowledgement and all path/backend declarations; this function
    still does not claim the supervisor transport is installed or ready.
    """
    env = os.environ if environ is None else environ
    mode = env.get("ARENA_RUNTIME_DEPLOYMENT", "disabled").strip().lower()
    if mode not in {"disabled", "staging", "production", "test-recovery"}:
        raise RuntimeDeploymentConfigError("ARENA_RUNTIME_DEPLOYMENT: dozwolone disabled, staging, test-recovery, production.")

    artifact_root = _absolute_path(
        env.get("ARENA_RUNTIME_ARTIFACT_ROOT", "/var/lib/arena-handoff"),
        "ARENA_RUNTIME_ARTIFACT_ROOT",
    )
    output_root = _absolute_path(
        env.get("ARENA_RUNTIME_OUTPUT_ROOT", "/run/arena-runtime"),
        "ARENA_RUNTIME_OUTPUT_ROOT",
    )
    worker_bundle = _absolute_path(
        env.get("ARENA_RUNTIME_WORKER_BUNDLE", "/usr/local/lib/ai-chess-arena-runtime"),
        "ARENA_RUNTIME_WORKER_BUNDLE",
    )

    if mode == "test-recovery":
        if env.get("ARENA_RUNTIME_TEST_ACK") != "I_UNDERSTAND_RECOVERY_ONLY_TEST":
            raise RuntimeDeploymentConfigError("Test-recovery wymaga jawnego ARENA_RUNTIME_TEST_ACK.")
        if env.get("ARENA_RUNTIME_BACKEND") != "systemd" or env.get("ARENA_RUNTIME_EXECUTION_ENABLED") != "0":
            raise RuntimeDeploymentConfigError("Test-recovery wymaga systemd i wyłączonego execution.")
        root = _absolute_path(env.get("ARENA_RUNTIME_TEST_ROOT", ""), "ARENA_RUNTIME_TEST_ROOT")
        root_path = PurePosixPath(root)
        if root_path.parent != PurePosixPath("/var/tmp"):
            raise RuntimeDeploymentConfigError("Test root must be a dedicated direct child of /var/tmp.")
        production_roots = (PurePosixPath("/var/lib/arena-handoff"), PurePosixPath("/run/arena-runtime"),
                            PurePosixPath("/usr/local/lib/ai-chess-arena-runtime"),
                            PurePosixPath("/run/ai-chess-arena-runtime"), PurePosixPath("/run/ai-chess-arena-api"))
        if any(root_path == item or root_path in item.parents or item in root_path.parents for item in production_roots):
            raise RuntimeDeploymentConfigError("Test root overlaps a production runtime path.")
        if artifact_root != f"{root}/data/handoff" or output_root != f"{root}/run/output":
            raise RuntimeDeploymentConfigError("Test-recovery paths must be derived from the isolated test root.")
        if worker_bundle != f"{root}/bundle":
            raise RuntimeDeploymentConfigError("Test-recovery worker bundle must remain inside the isolated test root.")
        if env.get("ARENA_DATABASE_PATH") != f"{root}/state/arena.sqlite3":
            raise RuntimeDeploymentConfigError("Test-recovery API database must use the isolated disposable DB.")
        if env.get("ARENA_LOG_DIR") != f"{root}/state/logs":
            raise RuntimeDeploymentConfigError("Test-recovery logs must remain inside the isolated test root.")
        if env.get("ARENA_RUNTIME_EXECUTOR_LOCK_PATH") != f"{root}/run/api/executor.lock":
            raise RuntimeDeploymentConfigError("Test-recovery executor lock must remain inside the isolated test root.")
        if env.get("ARENA_HTTP_HOST") != "127.0.0.1" or env.get("ARENA_HTTP_PORT") != "0":
            raise RuntimeDeploymentConfigError("Test-recovery API must bind loopback on an OS-assigned port.")
        if env.get("ARENA_BACKGROUND_JOBS", "").strip().lower() not in {"0", "false", "no", "off"}:
            raise RuntimeDeploymentConfigError("Test-recovery must disable background jobs.")
        socket_path = _absolute_path(env.get("ARENA_RUNTIME_SUPERVISOR_SOCKET", ""), "ARENA_RUNTIME_SUPERVISOR_SOCKET")
        lease_broker_socket = _absolute_path(env.get("ARENA_RUNTIME_LEASE_BROKER_SOCKET", ""), "ARENA_RUNTIME_LEASE_BROKER_SOCKET")
        if socket_path != f"{root}/run/supervisor/supervisor.sock" or lease_broker_socket != f"{root}/run/api/lease-broker.sock":
            raise RuntimeDeploymentConfigError("Test-recovery sockets must remain inside the isolated test root.")
        return RuntimeDeploymentConfig(
            environment="test-recovery", backend="systemd", execution_enabled=False,
            artifact_root=artifact_root, output_root=output_root, worker_bundle=worker_bundle,
            supervisor_socket=socket_path, lease_broker_socket=lease_broker_socket,
            unit_namespace="test-recovery",
        )

    if mode in {"disabled", "staging"}:
        # Never let an accidental global execution flag turn staging live.
        return RuntimeDeploymentConfig(
            environment=mode,
            backend="inert",
            execution_enabled=False,
            artifact_root=artifact_root,
            output_root=output_root,
            worker_bundle=worker_bundle,
            supervisor_socket=None,
            lease_broker_socket=None,
        )

    if env.get("ARENA_RUNTIME_PRODUCTION_ACK") != "I_UNDERSTAND_RUNTIME_EXECUTION":
        raise RuntimeDeploymentConfigError("Produkcja wymaga jawnego ARENA_RUNTIME_PRODUCTION_ACK.")
    if env.get("ARENA_RUNTIME_BACKEND") != "systemd":
        raise RuntimeDeploymentConfigError("Produkcja wymaga ARENA_RUNTIME_BACKEND=systemd.")
    if env.get("ARENA_RUNTIME_EXECUTION_ENABLED") != "1":
        raise RuntimeDeploymentConfigError("Produkcja wymaga jawnego ARENA_RUNTIME_EXECUTION_ENABLED=1.")

    socket_path = _absolute_path(
        env.get("ARENA_RUNTIME_SUPERVISOR_SOCKET", ""),
        "ARENA_RUNTIME_SUPERVISOR_SOCKET",
    )
    lease_broker_socket = _absolute_path(
        env.get("ARENA_RUNTIME_LEASE_BROKER_SOCKET", ""),
        "ARENA_RUNTIME_LEASE_BROKER_SOCKET",
    )
    if not socket_path.startswith("/run/"):
        raise RuntimeDeploymentConfigError("Socket supervisora musi znajdować się pod /run/.")
    if lease_broker_socket != "/run/ai-chess-arena-api/lease-broker.sock":
        raise RuntimeDeploymentConfigError("Broker dzierżaw wymaga dedykowanego socketu API pod /run/ai-chess-arena-api/.")
    if artifact_root != "/var/lib/arena-handoff":
        raise RuntimeDeploymentConfigError("Produkcja wymaga dedykowanego root artefaktów /var/lib/arena-handoff.")
    if output_root != "/run/arena-runtime":
        raise RuntimeDeploymentConfigError("Produkcja wymaga dedykowanego root wyników /run/arena-runtime.")
    if worker_bundle != "/usr/local/lib/ai-chess-arena-runtime":
        raise RuntimeDeploymentConfigError("Produkcja wymaga zatwierdzonego bundle pod /usr/local/lib/ai-chess-arena-runtime.")

    return RuntimeDeploymentConfig(
        environment="production",
        backend="systemd",
        execution_enabled=True,
        artifact_root=artifact_root,
        output_root=output_root,
        worker_bundle=worker_bundle,
        supervisor_socket=socket_path,
        lease_broker_socket=lease_broker_socket,
    )
