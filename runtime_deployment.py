"""Declarative deployment profile for the Issue #157 runtime integration.

This module is intentionally side-effect free: resolving a profile never opens
SQLite, touches systemd, creates files, or launches a process. It gives staging
and production one stable configuration contract while production wiring is
still being completed and independently reviewed.
"""
from __future__ import annotations

from dataclasses import dataclass
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
    """Resolve disabled/staging/production without enabling execution by itself.

    Default is disabled. Staging is deliberately inert. Production requires an
    explicit acknowledgement and all path/backend declarations; this function
    still does not claim the supervisor transport is installed or ready.
    """
    env = {} if environ is None else environ
    mode = env.get("ARENA_RUNTIME_DEPLOYMENT", "disabled").strip().lower()
    if mode not in {"disabled", "staging", "production"}:
        raise RuntimeDeploymentConfigError("ARENA_RUNTIME_DEPLOYMENT: dozwolone disabled, staging, production.")

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
