"""Build fail-closed systemd-run profiles for isolated, non-tournament model jobs.

This module only validates inputs and builds argv. It never starts a process,
downloads a model, changes systemd state, or touches tournament data.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

MODEL_ROOT = Path("/home/debian/ai-chess-arena/managed-models")
SHARED_MODEL_ROOT = Path("/home/debian/ai-agent/models")
JOB_ID_RE = re.compile(r"^[a-f0-9]{12,32}$")


@dataclass(frozen=True)
class SandboxLimits:
    cpu_quota_percent: int = 100
    memory_max_bytes: int = 2 * 1024**3
    runtime_max_seconds: int = 600
    tasks_max: int = 32


def validate_limits(limits: SandboxLimits) -> None:
    if not 25 <= limits.cpu_quota_percent <= 800:
        raise ValueError("CPUQuota musi mieścić się w zakresie 25–800%.")
    if not 512 * 1024**2 <= limits.memory_max_bytes <= 8 * 1024**3:
        raise ValueError("MemoryMax musi mieścić się w zakresie 512 MiB–8 GiB.")
    if not 10 <= limits.runtime_max_seconds <= 1800:
        raise ValueError("RuntimeMaxSec musi mieścić się w zakresie 10–1800 s.")
    if not 1 <= limits.tasks_max <= 128:
        raise ValueError("TasksMax musi mieścić się w zakresie 1–128.")


def validate_artifact(artifact_path: str | Path,
                      model_root: str | Path = MODEL_ROOT) -> Path:
    root = Path(model_root).resolve(strict=True)
    artifact = Path(artifact_path).resolve(strict=True)
    if not artifact.is_file():
        raise ValueError("Artefakt modelu musi być istniejącym zwykłym plikiem.")
    if artifact.suffix.lower() != ".gguf":
        raise ValueError("Do testów dopuszczone są wyłącznie artefakty GGUF.")
    if artifact == root or root not in artifact.parents:
        raise ValueError("Artefakt musi znajdować się wewnątrz prywatnego managed-models.")
    if SHARED_MODEL_ROOT == artifact or SHARED_MODEL_ROOT in artifact.parents:
        raise ValueError("Współdzielony katalog modeli jest bezwzględnie zabroniony.")
    return artifact


def build_systemd_run_argv(*, job_id: str, artifact_path: str | Path,
                           worker_argv: Sequence[str],
                           limits: SandboxLimits = SandboxLimits(),
                           model_root: str | Path = MODEL_ROOT,
                           systemd_run: str = "/usr/bin/systemd-run") -> list[str]:
    """Return a shell-free transient-service command; caller must separately authorize execution."""
    if not isinstance(job_id, str) or not JOB_ID_RE.fullmatch(job_id):
        raise ValueError("job_id musi być losowym identyfikatorem hex (12–32 znaków).")
    validate_limits(limits)
    artifact = validate_artifact(artifact_path, model_root)
    if not isinstance(worker_argv, (list, tuple)) or not worker_argv:
        raise ValueError("Brak polecenia zaufanego workera.")
    command = []
    for item in worker_argv:
        if not isinstance(item, str) or not item or "\x00" in item:
            raise ValueError("Nieprawidłowy argument workera.")
        command.append(item)
    if not Path(command[0]).is_absolute():
        raise ValueError("Ścieżka wykonywalna workera musi być bezwzględna.")

    unit = f"ai-chess-model-test-{job_id}"
    runtime_dir = f"/run/ai-chess-model-test-{job_id}"
    properties = [
        "Type=exec",
        "DynamicUser=yes",
        "NoNewPrivileges=yes",
        "PrivateTmp=yes",
        "PrivateDevices=yes",
        "PrivateNetwork=yes",
        "ProtectSystem=strict",
        "ProtectHome=read-only",
        "ProtectKernelTunables=yes",
        "ProtectKernelModules=yes",
        "ProtectControlGroups=yes",
        "ProtectClock=yes",
        "ProtectHostname=yes",
        "ProtectKernelLogs=yes",
        "RestrictSUIDSGID=yes",
        "LockPersonality=yes",
        "RestrictRealtime=yes",
        "SystemCallArchitectures=native",
        "RestrictAddressFamilies=AF_UNIX",
        "InaccessiblePaths=/home/debian/ai-agent/models",
        f"BindReadOnlyPaths={artifact}",
        f"RuntimeDirectory=ai-chess-model-test-{job_id}",
        "RuntimeDirectoryMode=0700",
        f"WorkingDirectory={runtime_dir}",
        f"CPUQuota={limits.cpu_quota_percent}%",
        f"MemoryMax={limits.memory_max_bytes}",
        f"RuntimeMaxSec={limits.runtime_max_seconds}",
        f"TasksMax={limits.tasks_max}",
        "KillMode=control-group",
        "TimeoutStopSec=5s",
        "UMask=0077",
    ]
    argv = [systemd_run, "--system", "--wait", "--pipe", "--collect",
            "--quiet", f"--unit={unit}"]
    argv.extend(f"--property={value}" for value in properties)
    argv.extend(["--", *command])
    return argv
