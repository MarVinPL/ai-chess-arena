"""Fail-closed adapter for exact transient systemd units.

No command is run at import time. Production use requires an explicitly
constructed backend in a privileged, separately reviewed supervisor process.
Tests inject a fake command runner.
"""
from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

from runtime_supervisor import RuntimeSupervisorError, _UNIT_RE
from runtime_systemd import RuntimeUnitSpec


class SystemdBackendError(RuntimeSupervisorError):
    pass


# Properties whose effective values must be verified after systemd accepts a unit.
_VERIFY_PROPERTIES = (
    "User", "Group", "NoNewPrivileges", "CapabilityBoundingSet",
    "AmbientCapabilities", "ProtectSystem", "ProtectHome", "PrivateTmp",
    "PrivateDevices", "PrivateNetwork", "ProtectControlGroups",
    "RestrictNamespaces", "RestrictAddressFamilies", "KillMode", "Delegate",
    "Type", "WorkingDirectory", "ReadOnlyPaths", "ReadWritePaths",
    "InaccessiblePaths", "ProtectKernelTunables", "ProtectKernelModules",
    "ProtectKernelLogs", "ProtectClock", "ProtectHostname", "ProtectProc",
    "ProcSubset", "RestrictSUIDSGID", "LockPersonality", "RestrictRealtime",
    "SystemCallArchitectures", "MemoryMax", "MemorySwapMax",
    "CPUQuotaPerSecUSec", "TasksMax", "LimitNOFILE", "RuntimeMaxUSec", "UMask",
)


def _run_checked(runner: Callable, argv: list[str], timeout: float) -> str:
    try:
        result = runner(argv, timeout)
    except Exception as exc:
        raise SystemdBackendError("Nie udało się wykonać kontrolowanego polecenia systemd.") from exc
    if getattr(result, "returncode", 1) != 0:
        stderr = getattr(result, "stderr", "")
        raise SystemdBackendError(f"Polecenie systemd zakończyło się błędem: {str(stderr)[:300]}")
    return getattr(result, "stdout", "")


class SystemdTransientBackend:
    """Start/stop only deterministic per-job units and verify cgroup teardown."""

    def __init__(self, *, command_runner=None, cgroup_root="/sys/fs/cgroup"):
        self._runner = command_runner or self._subprocess_runner
        self._cgroup_root = Path(cgroup_root)

    @staticmethod
    def _subprocess_runner(argv, timeout):
        return subprocess.run(argv, text=True, capture_output=True, timeout=timeout,
                              check=False, stdin=subprocess.DEVNULL, close_fds=True)

    @staticmethod
    def _validate(spec: RuntimeUnitSpec, argv: tuple[str, ...]):
        match = _UNIT_RE.fullmatch(spec.unit_name) if isinstance(spec, RuntimeUnitSpec) else None
        if not match:
            raise SystemdBackendError("Odmowa: nazwa jednostki poza ścisłym wzorcem.")
        job_id = int(match.group(1))
        if not argv or any(not isinstance(x, str) or not x or "\x00" in x for x in argv):
            raise SystemdBackendError("Odmowa: nieprawidłowe argv runtime.")
        if not argv[0].startswith("/usr/local/lib/ai-chess-arena-runtime/"):
            raise SystemdBackendError("Odmowa: worker musi pochodzić z root-owned runtime bundle.")
        props = spec.as_dict()
        if any("\n" in value or "\r" in value or "\x00" in value for value in props.values()):
            raise SystemdBackendError("Odmowa: znak sterujący w parametrach jednostki.")
        for key in ("ReadOnlyPaths", "ReadWritePaths", "InaccessiblePaths", "WorkingDirectory"):
            tokens = props.get(key, "").split()
            if not tokens or any(not token.startswith("/") or ".." in Path(token).parts for token in tokens):
                raise SystemdBackendError("Odmowa: nieprawidłowa ścieżka w parametrach systemd.")
        required = {
            "User": "arena-runtime", "Group": "arena-runtime",
            "NoNewPrivileges": "yes", "CapabilityBoundingSet": "",
            "AmbientCapabilities": "", "ProtectSystem": "strict",
            "ProtectHome": "yes", "PrivateNetwork": "yes",
            "KillMode": "control-group", "Delegate": "no",
            "WorkingDirectory": f"/run/arena-runtime/job-{job_id}",
            "ReadWritePaths": f"/run/arena-runtime/job-{job_id}",
        }
        if any(props.get(k) != v for k, v in required.items()):
            raise SystemdBackendError("Odmowa: osłabiona polityka izolacji jednostki.")
        return job_id

    def _show(self, unit_name: str) -> dict[str, str]:
        raw = _run_checked(self._runner, ["/usr/bin/systemctl", "show", "--no-pager",
                                          "--property=" + ",".join(_VERIFY_PROPERTIES), unit_name], 10)
        parsed = {}
        for line in raw.splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                parsed[key] = value
        return parsed

    @staticmethod
    def _expected(spec: RuntimeUnitSpec) -> dict[str, str]:
        p = spec.as_dict()
        expected = {key: value for key, value in p.items()
                    if key not in {"CPUQuota", "RuntimeMaxSec"}}
        expected["CPUQuotaPerSecUSec"] = str(int(p["CPUQuota"].rstrip("%")) * 10_000)
        expected["RuntimeMaxUSec"] = str(int(p["RuntimeMaxSec"]) * 1_000_000)
        return expected

    def start(self, spec: RuntimeUnitSpec, argv: tuple[str, ...]) -> None:
        self._validate(spec, argv)
        props = spec.as_dict()
        command = ["/usr/bin/systemd-run", "--system", "--no-block",
                   "--unit=" + spec.unit_name]
        for key, value in spec.properties:
            command.append("--property=" + key + "=" + value)
        command.extend(["--", *argv])
        _run_checked(self._runner, command, 15)
        actual = self._show(spec.unit_name)
        mismatches = [key for key, value in self._expected(spec).items()
                      if actual.get(key) != value]
        if mismatches:
            # Exact unit only; leave durable lease recoverable if stop is uncertain.
            raise SystemdBackendError("Systemd nie potwierdził wymaganych właściwości: " + ", ".join(mismatches))

    def _cgroup_is_empty(self, unit_name: str) -> bool:
        match = _UNIT_RE.fullmatch(unit_name)
        if not match:
            raise SystemdBackendError("Odmowa: nieprawidłowa nazwa jednostki.")
        shown = _run_checked(self._runner, ["/usr/bin/systemctl", "show", "--property=ControlGroup",
                                            "--value", unit_name], 10).strip()
        if not shown:
            return True  # systemd has no remaining cgroup for this exact unit
        if not shown.startswith("/system.slice/") or ".." in Path(shown).parts:
            raise SystemdBackendError("Niebezpieczna ścieżka cgroup zwrócona przez systemd.")
        root = self._cgroup_root.resolve()
        target = (root / shown.lstrip("/")).resolve()
        if root not in target.parents:
            raise SystemdBackendError("Cgroup jednostki wychodzi poza kontrolowany root.")
        procs = target / "cgroup.procs"
        try:
            return not procs.read_text(encoding="ascii").strip()
        except FileNotFoundError:
            return True
        except OSError as exc:
            raise SystemdBackendError("Nie można potwierdzić opróżnienia cgroup.") from exc

    def stop(self, unit_name: str) -> None:
        if not isinstance(unit_name, str) or not _UNIT_RE.fullmatch(unit_name):
            raise SystemdBackendError("Odmowa zatrzymania jednostki spoza ścisłego wzorca.")
        # systemctl stop targets only the persisted deterministic unit name.
        _run_checked(self._runner, ["/usr/bin/systemctl", "stop", unit_name], 30)
        if not self._cgroup_is_empty(unit_name):
            raise SystemdBackendError("Cgroup jednostki nadal zawiera procesy; lease pozostaje aktywny.")
