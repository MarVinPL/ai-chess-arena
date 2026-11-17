"""Root-owned runtime supervisor daemon entry point.

This module has no import-time host effects. It refuses to start unless every
production dependency is explicitly configured and verified.
"""
from __future__ import annotations

import grp
import hashlib
import os
import pwd
import stat
import sys
from pathlib import Path

from runtime_artifact_handoff import ArtifactHandoffError
from runtime_supervisor import RuntimeSupervisor
from runtime_lease_broker import RuntimeLeaseBrokerClient
from runtime_supervisor_authority import RuntimeJobAuthority, readonly_sqlite_factory
from runtime_supervisor_handlers import RuntimeSupervisorHandlers
from runtime_revocation_watchdog import RuntimeRevocationWatchdog
from runtime_supervisor_ipc import SupervisorIPCError
from runtime_systemd_backend import SystemdTransientBackend


class SupervisorServiceConfigError(RuntimeError):
    pass

SUPERVISOR_BUNDLE_PATH = Path("/usr/local/lib/ai-chess-arena-runtime/runtime_supervisor.pyz")


def _verify_supervisor_bundle(path: Path, expected_sha256: str, *,
                              running_path: str | Path | None = None,
                              expected_path: Path = SUPERVISOR_BUNDLE_PATH) -> None:
    try:
        canonical = path.resolve(strict=True)
    except OSError as exc:
        raise SupervisorServiceConfigError("Supervisor bundle path is unavailable.") from exc
    if path != expected_path or not path.is_absolute() or canonical != path:
        raise SupervisorServiceConfigError("Supervisor bundle path is not canonical.")
    if (len(expected_sha256) != 64
            or any(char not in "0123456789abcdef" for char in expected_sha256)):
        raise SupervisorServiceConfigError("Supervisor bundle SHA-256 pin is malformed.")
    try:
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)
                or info.st_uid != 0 or info.st_gid != 0
                or stat.S_IMODE(info.st_mode) & 0o022):
            raise SupervisorServiceConfigError(
                "Supervisor bundle must be root-owned and not group/world writable."
            )
        invoked = Path(sys.argv[0] if running_path is None else running_path)
        if invoked.resolve(strict=True) != path:
            raise SupervisorServiceConfigError(
                "Supervisor must be launched from the pinned immutable bundle."
            )
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    except SupervisorServiceConfigError:
        raise
    except OSError as exc:
        raise SupervisorServiceConfigError("Pinned supervisor bundle is unavailable.") from exc
    if digest.hexdigest() != expected_sha256:
        raise SupervisorServiceConfigError("Supervisor bundle SHA-256 mismatch.")


def _required(env: dict[str, str], key: str) -> str:
    value = env.get(key, "").strip()
    if not value or "\x00" in value:
        raise SupervisorServiceConfigError(f"Missing or invalid {key}.")
    return value


def _absolute(env: dict[str, str], key: str) -> Path:
    value = Path(_required(env, key))
    if not value.is_absolute() or ".." in value.parts:
        raise SupervisorServiceConfigError(f"{key} must be a canonical absolute path.")
    return value


def _identity(user_key: str, group_key: str, env: dict[str, str]) -> tuple[int, int]:
    try:
        user = pwd.getpwnam(_required(env, user_key))
        group = grp.getgrnam(_required(env, group_key))
    except KeyError as exc:
        raise SupervisorServiceConfigError("Configured service identity does not exist.") from exc
    if user.pw_uid == 0 or group.gr_gid == 0:
        raise SupervisorServiceConfigError("API/runtime identities must be unprivileged.")
    return user.pw_uid, group.gr_gid


def _verify_root(path: Path, *, mode: int, uid: int, gid: int, label: str) -> None:
    try:
        info = path.lstat()
    except OSError as exc:
        raise SupervisorServiceConfigError(f"{label} is not provisioned.") from exc
    if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
            or (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (uid, gid, mode)):
        raise SupervisorServiceConfigError(f"{label} ownership/mode does not match policy.")
    if path.resolve(strict=True) != path:
        raise SupervisorServiceConfigError(f"{label} is not canonical.")


def _verify_worker(path: Path, expected_sha256: str) -> None:
    if len(expected_sha256) != 64 or any(c not in "0123456789abcdef" for c in expected_sha256):
        raise SupervisorServiceConfigError("Worker SHA-256 pin is malformed.")
    try:
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)
                or info.st_uid != 0 or info.st_gid != 0
                or stat.S_IMODE(info.st_mode) & 0o022
                or not info.st_mode & stat.S_IXUSR):
            raise SupervisorServiceConfigError("Worker must be root-owned, executable and not group/world writable.")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise SupervisorServiceConfigError("Pinned worker bundle is unavailable.") from exc
    if digest.hexdigest() != expected_sha256:
        raise SupervisorServiceConfigError("Pinned worker bundle SHA-256 mismatch.")


def build_service(environ: dict[str, str] | None = None):
    """Build handlers and IPC server without starting a listener or systemd job."""
    env = dict(os.environ if environ is None else environ)
    if os.geteuid() != 0:
        raise SupervisorServiceConfigError("Supervisor daemon must run as root.")
    if env.get("ARENA_RUNTIME_DEPLOYMENT") != "production":
        raise SupervisorServiceConfigError("Supervisor daemon requires production deployment profile.")
    if env.get("ARENA_RUNTIME_PRODUCTION_ACK") != "I_UNDERSTAND_RUNTIME_EXECUTION":
        raise SupervisorServiceConfigError("Production acknowledgement is missing.")
    if env.get("ARENA_RUNTIME_BACKEND") != "systemd" or env.get("ARENA_RUNTIME_EXECUTION_ENABLED") != "1":
        raise SupervisorServiceConfigError("Systemd backend and explicit execution opt-in are required.")
    if not (sys.flags.isolated and sys.flags.no_site):
        raise SupervisorServiceConfigError(
            "Supervisor daemon must run with Python isolated/no-site mode (-I -S)."
        )

    bundle = _absolute(env, "ARENA_RUNTIME_SUPERVISOR_BUNDLE")
    if bundle != SUPERVISOR_BUNDLE_PATH:
        raise SupervisorServiceConfigError("Non-canonical supervisor bundle path is refused.")
    _verify_supervisor_bundle(
        bundle, _required(env, "ARENA_RUNTIME_SUPERVISOR_BUNDLE_SHA256")
    )

    database = _absolute(env, "ARENA_RUNTIME_DATABASE")
    socket_path = _absolute(env, "ARENA_RUNTIME_SUPERVISOR_SOCKET")
    lease_broker_socket = _absolute(env, "ARENA_RUNTIME_LEASE_BROKER_SOCKET")
    quarantine = _absolute(env, "ARENA_RUNTIME_QUARANTINE_ROOT")
    handoff = _absolute(env, "ARENA_RUNTIME_ARTIFACT_ROOT")
    output = _absolute(env, "ARENA_RUNTIME_OUTPUT_ROOT")
    worker = _absolute(env, "ARENA_RUNTIME_WORKER_PATH")
    if socket_path.parent != Path("/run/ai-chess-arena-runtime"):
        raise SupervisorServiceConfigError("Supervisor socket must use the dedicated /run directory.")
    if lease_broker_socket != Path("/run/ai-chess-arena-api/lease-broker.sock"):
        raise SupervisorServiceConfigError("Lease broker must use the dedicated API runtime socket.")
    if handoff != Path("/var/lib/arena-handoff") or output != Path("/run/arena-runtime"):
        raise SupervisorServiceConfigError("Non-canonical production handoff/output roots are refused.")
    if worker != Path("/usr/local/lib/ai-chess-arena-runtime/runtime_worker"):
        raise SupervisorServiceConfigError("Non-canonical worker path is refused.")

    api_uid, api_gid = _identity("ARENA_RUNTIME_API_USER", "ARENA_RUNTIME_API_GROUP", env)
    runtime_uid, runtime_gid = _identity("ARENA_RUNTIME_USER", "ARENA_RUNTIME_GROUP", env)
    if runtime_uid == api_uid or runtime_gid == api_gid:
        raise SupervisorServiceConfigError("API and runtime identities must be distinct.")
    supervisor_uid, supervisor_gid = os.getuid(), os.getgid()

    _verify_root(handoff, mode=0o711, uid=supervisor_uid, gid=supervisor_gid, label="Artifact handoff root")
    _verify_root(output, mode=0o755, uid=supervisor_uid, gid=supervisor_gid, label="Runtime output root")
    _verify_root(quarantine, mode=0o711, uid=api_uid, gid=api_gid, label="Artifact quarantine root")
    _verify_root(socket_path.parent, mode=0o750, uid=supervisor_uid, gid=api_gid, label="Supervisor socket directory")
    _verify_worker(worker, _required(env, "ARENA_RUNTIME_WORKER_SHA256"))

    raw_allowlist = _required(env, "ARENA_RUNTIME_ARTIFACT_ALLOWLIST")
    allowlist = frozenset(item.strip() for item in raw_allowlist.split(",") if item.strip())
    if not allowlist or len(allowlist) != len(raw_allowlist.split(",")):
        raise SupervisorServiceConfigError("Artifact allowlist must contain unique, non-empty exact keys.")

    # Authorization reads remain read-only. All lease writes are delegated
    # to the API-owned broker; the privileged daemon never opens SQLite rw.
    authority = RuntimeJobAuthority(
        connect_readonly=readonly_sqlite_factory(database),
        allowed_artifacts=allowlist,
        review_required_artifacts=frozenset(
            item.strip() for item in env.get("ARENA_RUNTIME_REVIEW_REQUIRED_ARTIFACTS", "").split(",") if item.strip()
        ),
    )
    leases = RuntimeLeaseBrokerClient(lease_broker_socket)
    supervisor = RuntimeSupervisor(
        backend=SystemdTransientBackend(), persist=leases.persist,
        set_state=leases.set_state, load_recoverable=leases.load_recoverable,
    )
    handlers = RuntimeSupervisorHandlers(
        authority=authority, supervisor=supervisor, lease_store=leases,
        quarantine_root=quarantine, handoff_root=handoff, output_root=output,
        runtime_uid=runtime_uid, runtime_gid=runtime_gid,
        api_uid=api_uid, api_gid=api_gid,
        supervisor_uid=supervisor_uid, supervisor_gid=supervisor_gid,
    )
    server = handlers.make_ipc_server(
        socket_path, allowed_uid=api_uid, socket_mode=0o660, socket_gid=api_gid,
    )
    server.revocation_watchdog = RuntimeRevocationWatchdog(
        authority=authority, lease_store=leases, handlers=handlers,
        interval_seconds=1.0,
        on_error=lambda message: print(
            f"ai-chess-arena-runtime-supervisor: {message}", file=sys.stderr, flush=True
        ),
    )
    return server


def main() -> int:
    try:
        server = build_service()
        watchdog = server.revocation_watchdog
        watchdog.start()
        try:
            server.serve_forever()
        finally:
            watchdog.stop()
    except (SupervisorServiceConfigError, SupervisorIPCError, OSError, RuntimeError) as exc:
        print(f"ai-chess-arena-runtime-supervisor: startup refused ({type(exc).__name__})", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
