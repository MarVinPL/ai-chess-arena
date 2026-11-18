"""Fail-closed adapter for exact transient systemd units.

No command is run at import time. Production use requires an explicitly
constructed backend in a privileged, separately reviewed supervisor process.
Tests inject a fake command runner.
"""
from __future__ import annotations

import re
import subprocess
import time
from collections.abc import Callable
from decimal import Decimal, InvalidOperation
from pathlib import Path

from runtime_supervisor import RuntimeSupervisorError, _UNIT_RE
from runtime_systemd import RuntimeUnitSpec, build_runtime_unit_spec


class SystemdBackendError(RuntimeSupervisorError):
    pass


# Properties whose effective values must be verified after systemd accepts a unit.
_VERIFY_PROPERTIES = (
    "ActiveState", "User", "Group", "NoNewPrivileges", "CapabilityBoundingSet",
    "AmbientCapabilities", "ProtectSystem", "ProtectHome", "PrivateTmp",
    "PrivateDevices", "PrivateNetwork", "ProtectControlGroups",
    "RestrictNamespaces", "RestrictAddressFamilies", "KillMode", "Delegate",
    "Type", "WorkingDirectory", "Environment", "ReadOnlyPaths",
    "BindReadOnlyPaths", "ReadWritePaths",
    "InaccessiblePaths", "ProtectKernelTunables", "ProtectKernelModules",
    "ProtectKernelLogs", "ProtectClock", "ProtectHostname", "ProtectProc",
    "ProcSubset", "RestrictSUIDSGID", "LockPersonality", "RestrictRealtime",
    "SystemCallArchitectures", "MemoryMax", "MemorySwapMax",
    "CPUQuotaPerSecUSec", "TasksMax", "LimitNOFILE", "RuntimeMaxUSec", "UMask",
)


_TIMESPAN_TOKEN = re.compile(r"(\d+(?:\.\d+)?)(us|µs|ms|s|min|h|d|w)")


def _timespan_to_usec(value: str) -> int | None:
    """Parse systemd's human-readable timespans or its numeric usec form."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if value.isdigit():
        return int(value)
    factors = {
        "us": 1, "µs": 1, "ms": 1_000, "s": 1_000_000,
        "min": 60_000_000, "h": 3_600_000_000,
        "d": 86_400_000_000, "w": 604_800_000_000,
    }
    tokens = list(_TIMESPAN_TOKEN.finditer(value))
    if not tokens or "".join(match.group(0) for match in tokens) != value.replace(" ", ""):
        return None
    try:
        total = sum((Decimal(match.group(1)) * factors[match.group(2)]
                     for match in tokens), Decimal(0))
    except InvalidOperation:
        return None
    if total != total.to_integral_value():
        return None
    return int(total)


def _property_matches(key: str, actual: str | None, expected: str) -> bool:
    if actual is None:
        return False
    if key == "RestrictAddressFamilies":
        return set(actual.split()) == set(expected.split())
    if key == "BindReadOnlyPaths":
        return actual in {expected, expected + ":rbind"}
    if key in {"CPUQuotaPerSecUSec", "RuntimeMaxUSec"}:
        return _timespan_to_usec(actual) == int(expected)
    return actual == expected


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

    def __init__(self, *, command_runner=None, cgroup_root="/sys/fs/cgroup", unit_namespace="production"):
        if unit_namespace not in {"production", "test-recovery"}:
            raise ValueError("Unsupported systemd unit namespace.")
        self._unit_namespace = unit_namespace
        self._unit_re = _UNIT_RE if unit_namespace == "production" else re.compile(r"^ai-chess-arena-runtime-test-([1-9][0-9]*)\.service$")
        self._runner = command_runner or self._subprocess_runner
        self._cgroup_root = Path(cgroup_root)

    @staticmethod
    def _subprocess_runner(argv, timeout):
        return subprocess.run(argv, text=True, capture_output=True, timeout=timeout,
                              check=False, stdin=subprocess.DEVNULL, close_fds=True)

    def _validate(self, spec: RuntimeUnitSpec, argv: tuple[str, ...]):
        match = self._unit_re.fullmatch(spec.unit_name) if isinstance(spec, RuntimeUnitSpec) else None
        if not match:
            raise SystemdBackendError("Odmowa: nazwa jednostki poza ścisłym wzorcem.")
        job_id = int(match.group(1))
        if not isinstance(argv, (tuple, list)) or len(argv) != 3 or any(
            not isinstance(x, str) or not x or "\\x00" in x for x in argv
        ):
            raise SystemdBackendError("Odmowa: nieprawidłowe argv runtime.")
        expected_worker = "/usr/local/lib/ai-chess-arena-runtime/runtime_worker"
        if tuple(argv) != (expected_worker, "--job", str(job_id)):
            raise SystemdBackendError("Odmowa: polecenie workera nie odpowiada przypisanemu zadaniu.")

        props = spec.as_dict()
        # Rebuild the entire policy from validated primitive values. This prevents
        # callers from weakening a security property while retaining a valid unit name.
        try:
            read_only = props["ReadOnlyPaths"].split()
            bind_source, bind_target = props["BindReadOnlyPaths"].split(":", 1)
            environment = {}
            for item in props["Environment"].split():
                key, value = item.split("=", 1)
                if key in environment:
                    raise ValueError("duplicate runtime environment key")
                environment[key] = value
            api_uid = int(environment["ARENA_API_UID"])
            api_gid = int(environment["ARENA_API_GID"])
            base_keys = {"ARENA_API_UID", "ARENA_API_GID"}
            test_keys = {"ARENA_RUNTIME_TEST_CRASH_MODE", "ARENA_RUNTIME_TEST_ACK"}
            test_worker_crash = False
            if set(environment) == base_keys:
                pass
            elif (self._unit_namespace == "test-recovery"
                  and set(environment) == base_keys | test_keys
                  and environment["ARENA_RUNTIME_TEST_CRASH_MODE"] == "SIGKILL_BEFORE_MODEL_EXEC"
                  and environment["ARENA_RUNTIME_TEST_ACK"] == "I_UNDERSTAND_WORKER_CRASH_TEST"):
                test_worker_crash = True
            else:
                raise ValueError("unexpected runtime environment")
            quota = props["CPUQuota"]
            if not quota.endswith("%") or not quota[:-1].isdigit():
                raise ValueError("invalid CPU quota")
            expected_spec = build_runtime_unit_spec(
                job_id=job_id,
                runtime_user=props["User"], runtime_group=props["Group"],
                artifact_path=bind_source, artifact_mount_path=bind_target,
                output_dir=props["ReadWritePaths"], api_uid=api_uid, api_gid=api_gid,
                memory_max_bytes=int(props["MemoryMax"]),
                memory_swap_max_bytes=int(props["MemorySwapMax"]),
                cpu_quota_percent=int(quota[:-1]), tasks_max=int(props["TasksMax"]),
                runtime_seconds=int(props["RuntimeMaxSec"]),
                test_worker_crash=test_worker_crash,
            )
        except (KeyError, TypeError, ValueError, IndexError) as exc:
            raise SystemdBackendError("Odmowa: niekompletna lub nieprawidłowa specyfikacja jednostki.") from exc
        if (spec.properties != expected_spec.properties or read_only != ["/usr/local/lib/ai-chess-arena-runtime"]
                or "/var/lib/arena-handoff" not in props["InaccessiblePaths"].split()):
            raise SystemdBackendError("Odmowa: specyfikacja odbiega od kompletnej polityki izolacji.")
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
        expected["ActiveState"] = "active"
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
        # --no-block acknowledges job submission, not successful process startup.
        # Wait for the exact unit to become active, or fail closed on terminal state.
        deadline = time.monotonic() + 15
        actual = {}
        while True:
            actual = self._show(spec.unit_name)
            state = actual.get("ActiveState")
            if state == "active" or state in {"failed", "inactive"} or state is None:
                break
            if time.monotonic() >= deadline:
                break
            time.sleep(0.1)
        mismatches = [key for key, value in self._expected(spec).items()
                      if not _property_matches(key, actual.get(key), value)]
        if mismatches:
            # Exact unit only; leave durable lease recoverable if stop is uncertain.
            raise SystemdBackendError("Systemd nie potwierdził wymaganych właściwości: " + ", ".join(mismatches))

    def _unit_snapshot(self, unit_name: str) -> dict[str, str]:
        raw = _run_checked(
            self._runner,
            ["/usr/bin/systemctl", "show", "--no-pager",
             "--property=LoadState,ActiveState,ControlGroup", unit_name],
            10,
        )
        parsed = {}
        for line in raw.splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                parsed[key] = value
        return parsed

    @staticmethod
    def _unit_is_gone_or_terminal_without_cgroup(snapshot: dict[str, str]) -> bool:
        return (
            not snapshot.get("ControlGroup")
            and (
                snapshot.get("LoadState") == "not-found"
                or snapshot.get("ActiveState") in {"inactive", "failed"}
            )
        )

    def _cgroup_is_empty(self, unit_name: str) -> bool:
        match = self._unit_re.fullmatch(unit_name)
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
        # A vanished cgroup means systemd has fully removed this exact unit's
        # control group. If the directory still exists, missing controller
        # files are not evidence of emptiness: fail closed. cgroup.procs only
        # lists direct members, while cgroup.events populated includes nested
        # descendants on cgroup v2.
        if not target.exists():
            return True
        if not target.is_dir():
            raise SystemdBackendError("Ścieżka cgroup jednostki nie jest katalogiem.")
        procs = target / "cgroup.procs"
        events = target / "cgroup.events"
        try:
            direct_members = procs.read_text(encoding="ascii").strip()
            event_lines = events.read_text(encoding="ascii").splitlines()
            populated_values = [line.split()[1] for line in event_lines
                                if len(line.split()) == 2 and line.split()[0] == "populated"]
            if len(populated_values) != 1 or populated_values[0] not in {"0", "1"}:
                raise ValueError("missing or malformed populated field")
            return not direct_members and populated_values[0] == "0"
        except (OSError, ValueError, IndexError) as exc:
            raise SystemdBackendError("Nie można jednoznacznie potwierdzić opróżnienia cgroup v2.") from exc

    def stop(self, unit_name: str) -> None:
        if not isinstance(unit_name, str) or not self._unit_re.fullmatch(unit_name):
            raise SystemdBackendError("Odmowa zatrzymania jednostki spoza ścisłego wzorca.")
        # A completed transient unit may already have been garbage-collected.
        # In that case systemd's not-found + empty ControlGroup is positive
        # evidence that there is no unit left to stop. Do not turn this normal
        # terminal state into a permanently pending recovery lease.
        snapshot = self._unit_snapshot(unit_name)
        if self._unit_is_gone_or_terminal_without_cgroup(snapshot):
            return
        # The unit may disappear between the snapshot and stop. Re-read exact
        # unit state after a stop error and accept only a terminal/no-cgroup
        # state; all ambiguous states remain fail-closed.
        try:
            _run_checked(self._runner, ["/usr/bin/systemctl", "stop", unit_name], 30)
        except SystemdBackendError:
            after_error = self._unit_snapshot(unit_name)
            if self._unit_is_gone_or_terminal_without_cgroup(after_error):
                return
            raise
        if not self._cgroup_is_empty(unit_name):
            raise SystemdBackendError("Cgroup jednostki nadal zawiera procesy; lease pozostaje aktywny.")
