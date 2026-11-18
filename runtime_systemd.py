"""Pure policy builder for a future per-job systemd runtime unit.

This module deliberately does not invoke systemd, create accounts, or launch a
process. The API/worker integration must persist the returned unit name before
asking a privileged supervisor to start it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath


_UNIT_PREFIX = "ai-chess-arena-runtime-"
_ACCOUNT_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,30}\$?$")


class RuntimeUnitPolicyError(ValueError):
    """Raised when a proposed transient runtime unit violates policy."""


@dataclass(frozen=True)
class RuntimeUnitSpec:
    unit_name: str
    properties: tuple[tuple[str, str], ...]

    def as_dict(self) -> dict[str, str]:
        return dict(self.properties)


def _absolute_path(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.startswith("/") or "\x00" in value:
        raise RuntimeUnitPolicyError(f"{field} musi być bezwzględną ścieżką POSIX.")
    path = PurePosixPath(value)
    if ".." in path.parts:
        raise RuntimeUnitPolicyError(f"{field} nie może zawierać '..'.")
    return str(path)


def build_runtime_unit_spec(*, job_id: int, runtime_user: str,
                            runtime_group: str, artifact_path: str,
                            artifact_mount_path: str, output_dir: str,
                            api_uid: int, api_gid: int,
                            memory_max_bytes: int,
                            memory_swap_max_bytes: int, cpu_quota_percent: int,
                            tasks_max: int, runtime_seconds: int,
                            test_worker_crash: bool = False) -> RuntimeUnitSpec:
    """Build a fail-closed policy spec; it does not execute or validate host state."""
    if isinstance(job_id, bool) or not isinstance(job_id, int) or job_id < 1:
        raise RuntimeUnitPolicyError("Nieprawidłowy identyfikator zadania.")
    if runtime_user != "arena-runtime" or not _ACCOUNT_RE.fullmatch(runtime_user):
        raise RuntimeUnitPolicyError("Wymagane jest dedykowane konto nieuprzywilejowane arena-runtime.")
    if runtime_group != "arena-runtime" or not _ACCOUNT_RE.fullmatch(runtime_group):
        raise RuntimeUnitPolicyError("Wymagana jest dedykowana grupa arena-runtime.")

    artifact = _absolute_path(artifact_path, "artifact_path")
    output = _absolute_path(output_dir, "output_dir")
    if not artifact.startswith(f"/var/lib/arena-handoff/job-{job_id}/"):
        raise RuntimeUnitPolicyError("Artefakt musi pochodzić z katalogu przekazania przypisanego do tego zadania.")
    if output != f"/run/arena-runtime/job-{job_id}":
        raise RuntimeUnitPolicyError("Katalog zapisu musi być prywatnym katalogiem /run dla tego zadania.")
    if (type(api_uid) is not int or api_uid < 1
            or type(api_gid) is not int or api_gid < 1):
        raise RuntimeUnitPolicyError("Wymagane są nieuprzywilejowane identyfikatory API.")
    runtime_root = "/usr/local/lib/ai-chess-arena-runtime"
    mount_path = _absolute_path(artifact_mount_path, "artifact_mount_path")
    if mount_path != output + "/model.gguf":
        raise RuntimeUnitPolicyError("Artefakt musi być montowany wyłącznie jako model.gguf w katalogu zadania.")
    if artifact == output or artifact.startswith(output.rstrip("/") + "/"):
        raise RuntimeUnitPolicyError("Artefakt źródłowy nie może znajdować się wewnątrz katalogu zapisu runtime.")

    numeric = {
        "memory_max_bytes": (memory_max_bytes, 256 * 1024 * 1024, 64 * 1024**3),
        "memory_swap_max_bytes": (memory_swap_max_bytes, 0, 64 * 1024**3),
        "cpu_quota_percent": (cpu_quota_percent, 1, 200),
        "tasks_max": (tasks_max, 1, 128),
        "runtime_seconds": (runtime_seconds, 1, 1800),
    }
    for name, (value, minimum, maximum) in numeric.items():
        if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
            raise RuntimeUnitPolicyError(f"{name} poza dozwolonym zakresem.")

    if type(test_worker_crash) is not bool:
        raise RuntimeUnitPolicyError("test_worker_crash must be an explicit boolean.")

    # No ambient host access: a future supervisor must provide only these paths.
    environment = f"ARENA_API_UID={api_uid} ARENA_API_GID={api_gid}"
    if test_worker_crash:
        environment += (
            " ARENA_RUNTIME_TEST_CRASH_MODE=SIGKILL_BEFORE_MODEL_EXEC"
            " ARENA_RUNTIME_TEST_ACK=I_UNDERSTAND_WORKER_CRASH_TEST"
        )
    props = (
        ("User", runtime_user),
        ("Group", runtime_group),
        ("Type", "exec"),
        ("NoNewPrivileges", "yes"),
        ("CapabilityBoundingSet", ""),
        ("AmbientCapabilities", ""),
        ("ProtectSystem", "strict"),
        ("ProtectHome", "yes"),
        ("PrivateTmp", "yes"),
        ("PrivateDevices", "yes"),
        ("PrivateNetwork", "yes"),
        ("ProtectKernelTunables", "yes"),
        ("ProtectKernelModules", "yes"),
        ("ProtectControlGroups", "yes"),
        ("ProtectKernelLogs", "yes"),
        ("ProtectClock", "yes"),
        ("ProtectHostname", "yes"),
        ("ProtectProc", "invisible"),
        ("ProcSubset", "pid"),
        ("RestrictSUIDSGID", "yes"),
        ("LockPersonality", "yes"),
        ("RestrictRealtime", "yes"),
        ("RestrictNamespaces", "yes"),
        ("SystemCallArchitectures", "native"),
        ("RestrictAddressFamilies", "AF_UNIX AF_INET"),
        ("WorkingDirectory", output),
        ("Environment", environment),
        ("KillMode", "control-group"),
        ("Delegate", "no"),
        ("MemoryMax", str(memory_max_bytes)),
        ("MemorySwapMax", str(memory_swap_max_bytes)),
        ("CPUQuota", f"{cpu_quota_percent}%"),
        ("TasksMax", str(tasks_max)),
        ("LimitNOFILE", "64"),
        ("RuntimeMaxSec", str(runtime_seconds)),
        ("UMask", "0077"),
        ("ReadOnlyPaths", runtime_root),
        ("BindReadOnlyPaths", f"{artifact}:{mount_path}"),
        ("ReadWritePaths", output),
        ("InaccessiblePaths", "/home /root /etc/ai-chess-arena /etc/ssh /etc/ssl/private /run/systemd/private /run/systemd/notify /run/dbus /run/credentials /var/lib/ai-chess-arena /var/lib/arena-handoff"),
    )
    return RuntimeUnitSpec(
        unit_name=f"{_UNIT_PREFIX}{job_id}.service",
        properties=props,
    )
