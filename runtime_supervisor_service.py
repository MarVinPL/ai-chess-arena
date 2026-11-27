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
import socket
import threading
import time
import signal
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


def _build_recovery_test_service(env: dict[str, str]):
    """Build an isolated, recovery-only supervisor; it cannot launch workers."""
    if env.get("ARENA_RUNTIME_TEST_ACK") != "I_UNDERSTAND_RECOVERY_ONLY_TEST":
        raise SupervisorServiceConfigError("Recovery-only test acknowledgement is missing.")
    if env.get("ARENA_RUNTIME_BACKEND") != "systemd" or env.get("ARENA_RUNTIME_EXECUTION_ENABLED") != "0":
        raise SupervisorServiceConfigError("Test profile requires systemd recovery with execution disabled.")
    if not (sys.flags.isolated and sys.flags.no_site):
        raise SupervisorServiceConfigError("Supervisor daemon must run with Python isolated/no-site mode (-I -S).")

    root = _absolute(env, "ARENA_RUNTIME_TEST_ROOT")
    api_uid, api_gid = _identity("ARENA_RUNTIME_API_USER", "ARENA_RUNTIME_API_GROUP", env)
    runtime_uid, runtime_gid = _identity("ARENA_RUNTIME_USER", "ARENA_RUNTIME_GROUP", env)
    if runtime_uid == api_uid or runtime_gid == api_gid:
        raise SupervisorServiceConfigError("API and runtime identities must be distinct.")
    supervisor_uid, supervisor_gid = os.getuid(), os.getgid()
    _verify_root(root, mode=0o710, uid=0, gid=api_gid, label="Recovery test root")
    bundle = root / "bundle" / "runtime_supervisor.pyz"
    database = root / "state" / "arena.sqlite3"
    socket_path = root / "run" / "supervisor" / "supervisor.sock"
    broker_socket = root / "run" / "api" / "lease-broker.sock"
    quarantine = root / "data" / "quarantine"
    handoff = root / "data" / "handoff"
    output = root / "run" / "output"
    expected = (bundle, database, socket_path, broker_socket, quarantine, handoff, output)
    configured_paths = {
        "ARENA_RUNTIME_DATABASE": database,
        "ARENA_RUNTIME_SUPERVISOR_SOCKET": socket_path,
        "ARENA_RUNTIME_LEASE_BROKER_SOCKET": broker_socket,
        "ARENA_RUNTIME_QUARANTINE_ROOT": quarantine,
        "ARENA_RUNTIME_ARTIFACT_ROOT": handoff,
        "ARENA_RUNTIME_OUTPUT_ROOT": output,
    }
    if any(_absolute(env, key) != expected_path for key, expected_path in configured_paths.items()):
        raise SupervisorServiceConfigError("Test profile environment paths do not match its isolated root.")
    if root.parent != Path("/var/tmp") or any(path == root or root not in path.parents for path in expected):
        raise SupervisorServiceConfigError("Test profile root must be a dedicated direct child of /var/tmp and contain every test path.")
    production_paths = {
        SUPERVISOR_BUNDLE_PATH, Path("/usr/local/lib/ai-chess-arena-runtime/runtime_worker"),
        Path("/var/lib/arena-handoff"), Path("/run/arena-runtime"),
        Path("/run/ai-chess-arena-runtime"), Path("/run/ai-chess-arena-api"),
        Path("/var/lib/ai-chess-arena/quarantine"),
    }
    if any(path == production or path in production.parents or production in path.parents
           for path in expected for production in production_paths):
        raise SupervisorServiceConfigError("Test profile overlaps a production runtime path.")

    _verify_root(bundle.parent, mode=0o700, uid=0, gid=0, label="Test immutable bundle directory")
    _verify_root(database.parent, mode=0o700, uid=api_uid, gid=api_gid, label="Test API state directory")
    _verify_root(root / "data", mode=0o710, uid=0, gid=api_gid, label="Test data directory")
    _verify_root(root / "run", mode=0o710, uid=0, gid=api_gid, label="Test runtime directory")
    _verify_supervisor_bundle(
        bundle, _required(env, "ARENA_RUNTIME_SUPERVISOR_BUNDLE_SHA256"),
        expected_path=bundle,
    )
    _verify_root(handoff, mode=0o711, uid=supervisor_uid, gid=supervisor_gid, label="Test artifact handoff root")
    _verify_root(output, mode=0o755, uid=supervisor_uid, gid=supervisor_gid, label="Test runtime output root")
    _verify_root(quarantine, mode=0o711, uid=api_uid, gid=api_gid, label="Test artifact quarantine root")
    _verify_root(socket_path.parent, mode=0o750, uid=supervisor_uid, gid=api_gid, label="Test supervisor socket directory")
    _verify_root(broker_socket.parent, mode=0o750, uid=api_uid, gid=api_gid, label="Test API broker socket directory")
    try:
        db_info = database.lstat()
        if (not stat.S_ISREG(db_info.st_mode) or stat.S_ISLNK(db_info.st_mode)
                or (db_info.st_uid, db_info.st_gid, stat.S_IMODE(db_info.st_mode))
                != (api_uid, api_gid, 0o600) or database.resolve(strict=True) != database):
            raise SupervisorServiceConfigError("Test database must be a canonical API-owned 0600 regular file.")
    except OSError as exc:
        raise SupervisorServiceConfigError("Disposable test database is not provisioned.") from exc

    raw_allowlist = _required(env, "ARENA_RUNTIME_ARTIFACT_ALLOWLIST")
    allowlist = frozenset(item.strip() for item in raw_allowlist.split(",") if item.strip())
    if not allowlist or len(allowlist) != len(raw_allowlist.split(",")):
        raise SupervisorServiceConfigError("Artifact allowlist must contain unique, non-empty exact keys.")
    authority = RuntimeJobAuthority(
        connect_readonly=readonly_sqlite_factory(database),
        allowed_artifacts=allowlist,
        review_required_artifacts=frozenset(
            item.strip() for item in env.get("ARENA_RUNTIME_REVIEW_REQUIRED_ARTIFACTS", "").split(",") if item.strip()
        ),
    )
    leases = RuntimeLeaseBrokerClient(broker_socket, unit_namespace="test-recovery")
    supervisor = RuntimeSupervisor(
        backend=SystemdTransientBackend(unit_namespace="test-recovery"), persist=leases.persist,
        set_state=leases.set_state, load_recoverable=leases.load_recoverable,
        unit_namespace="test-recovery",
    )
    handlers = RuntimeSupervisorHandlers(
        authority=authority, supervisor=supervisor, lease_store=leases,
        quarantine_root=quarantine, handoff_root=handoff, output_root=output,
        runtime_uid=runtime_uid, runtime_gid=runtime_gid,
        api_uid=api_uid, api_gid=api_gid,
        supervisor_uid=supervisor_uid, supervisor_gid=supervisor_gid,
        allow_start=False,
    )
    server = handlers.make_ipc_server(
        socket_path, allowed_uid=api_uid, socket_mode=0o660, socket_gid=api_gid,
    )
    server.recovery_only_test_profile = True
    server.revocation_watchdog = RuntimeRevocationWatchdog(
        authority=authority, lease_store=leases, handlers=handlers,
        interval_seconds=1.0,
        on_error=lambda message: print(
            f"ai-chess-arena-runtime-supervisor: {message}", file=sys.stderr, flush=True
        ),
    )
    return server


def build_service(environ: dict[str, str] | None = None):
    """Build handlers and IPC server without starting a listener or systemd job."""
    env = dict(os.environ if environ is None else environ)
    if os.geteuid() != 0:
        raise SupervisorServiceConfigError("Supervisor daemon must run as root.")
    deployment = env.get("ARENA_RUNTIME_DEPLOYMENT")
    if deployment == "test-recovery":
        return _build_recovery_test_service(env)
    if deployment != "production":
        raise SupervisorServiceConfigError("Supervisor daemon requires production deployment or explicit test-recovery profile.")
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


def _notify_systemd(message: str) -> None:
    """Send a readiness/lifecycle datagram to systemd without third-party deps."""
    address = os.environ.get("NOTIFY_SOCKET", "")
    if not address:
        raise SupervisorServiceConfigError("systemd notification socket is missing.")
    if address.startswith("@"):
        address = "\0" + address[1:]
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as client:
        client.sendto(message.encode("utf-8"), address)


def main() -> int:
    try:
        server = build_service()
        watchdog = server.revocation_watchdog
        if not getattr(server, "recovery_only_test_profile", False):
            watchdog.start()
        failures = []

        def serve():
            try:
                server.serve_forever()
            except BaseException as exc:
                failures.append(exc)

        listener = threading.Thread(
            target=serve, name="arena-runtime-supervisor-ipc", daemon=False,
        )
        shutdown_requested = threading.Event()

        def request_shutdown(_signum, _frame):
            # Run shutdown from the main thread; socketserver requires its
            # serve_forever loop to be running in another thread.
            shutdown_requested.set()
            if server.ready.is_set():
                server.shutdown()

        previous_sigterm = signal.signal(signal.SIGTERM, request_shutdown)
        previous_sigint = signal.signal(signal.SIGINT, request_shutdown)
        listener.start()
        deadline = time.monotonic() + 15.0
        while listener.is_alive() and not server.ready.wait(0.1):
            if time.monotonic() >= deadline:
                raise SupervisorServiceConfigError("Supervisor IPC listener readiness timed out.")
        if failures:
            raise failures[0]
        if shutdown_requested.is_set():
            if server.ready.is_set():
                server.shutdown()
            listener.join()
            return 0
        if not server.ready.is_set():
            raise SupervisorServiceConfigError("Supervisor IPC listener exited before readiness.")
        # Type=notify consumers (the API unit) may start only after bind/listen
        # and the first active poll-loop iteration, not merely after Python exec.
        _notify_systemd("READY=1\nSTATUS=Supervisor IPC socket is accepting API peers")
        listener.join()
        if not shutdown_requested.is_set():
            raise RuntimeError("Supervisor IPC listener exited unexpectedly.")
    except (SupervisorServiceConfigError, SupervisorIPCError, OSError, RuntimeError) as exc:
        print(f"ai-chess-arena-runtime-supervisor: startup/runtime failure ({type(exc).__name__})", file=sys.stderr, flush=True)
        return 1
    finally:
        if "previous_sigterm" in locals():
            signal.signal(signal.SIGTERM, previous_sigterm)
            signal.signal(signal.SIGINT, previous_sigint)
        if "watchdog" in locals():
            watchdog.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
