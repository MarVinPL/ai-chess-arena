#!/usr/bin/python3
"""Fixed job-ID-only worker entrypoint for the isolated systemd runtime bundle.

Install this file as /usr/local/lib/ai-chess-arena-runtime/runtime_worker.
The bundle, its Python modules and llamafile must be root-owned and read-only.
"""
from __future__ import annotations

import argparse
import grp
import hashlib
import os
import queue
import resource
import signal
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path

from runtime_resource_policy import MAX_ARTIFACT_BYTES
from runtime_socket_handoff import verify_handoff
from runtime_unix_bridge import UnixRuntimeBridge

BUNDLE_ROOT = Path("/usr/local/lib/ai-chess-arena-runtime")
WORKER_PATH = BUNDLE_ROOT / "runtime_worker"
SUPERVISOR_GROUP = "debian"
LLAMAFILE = BUNDLE_ROOT / "llamafile"
LLAMAFILE_SHA256 = "756bf97febae557084e8ced9bde7a1cb0c7f6ce778530d582afa219406237a50"
LOOPBACK_PORT = 18080
MAX_CPU_SECONDS = 1802
MAX_LOG_BYTES = 2 * 1024 * 1024


class WorkerError(RuntimeError):
    pass


def _digest_fd(fd: int) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    os.lseek(fd, 0, os.SEEK_SET)
    while True:
        block = os.read(fd, 1024 * 1024)
        if not block:
            break
        size += len(block)
        digest.update(block)
    os.lseek(fd, 0, os.SEEK_SET)
    return digest.hexdigest(), size


def _verify_readonly_file(path: Path, *, expected_uid: int,
                          expected_sha256: str | None = None,
                          maximum_size: int = MAX_ARTIFACT_BYTES) -> tuple[str, int]:
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != expected_uid
                or stat.S_IMODE(info.st_mode) != 0o400
                or not 1 <= info.st_size <= maximum_size):
            raise WorkerError("Read-only file does not match the runtime bundle contract.")
        digest, size = _digest_fd(fd)
        if size != info.st_size or (expected_sha256 and digest != expected_sha256):
            raise WorkerError("File size or SHA-256 verification failed.")
        return digest, size
    finally:
        os.close(fd)


def _verify_binary() -> None:
    fd = os.open(LLAMAFILE, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0
                or not (info.st_mode & 0o111) or (info.st_mode & 0o022)):
            raise WorkerError("Pinned llamafile binary permissions are invalid.")
        digest, _size = _digest_fd(fd)
        if digest != LLAMAFILE_SHA256:
            raise WorkerError("Pinned llamafile binary SHA-256 mismatch.")
    finally:
        os.close(fd)


def _parse_job_id(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--job", required=True, type=int)
    args = parser.parse_args(argv)
    if args.job < 1:
        raise WorkerError("Invalid job ID.")
    return args.job


def _fixed_command(model_path: Path) -> list[str]:
    with LLAMAFILE.open("rb") as stream:
        is_elf = stream.read(4) == b"\x7fELF"
    command = [str(LLAMAFILE)] if is_elf else ["/bin/sh", str(LLAMAFILE)]
    command += [
        "-m", str(model_path), "--server", "--host", "127.0.0.1",
        "--port", str(LOOPBACK_PORT), "--threads", "1", "--ctx-size", "2048",
        "--parallel", "1", "--temp", "0", "--no-ui", "--no-warmup",
    ]
    return command


def _limits() -> None:
    resource.setrlimit(resource.RLIMIT_CPU, (MAX_CPU_SECONDS, MAX_CPU_SECONDS))
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_LOG_BYTES, MAX_LOG_BYTES))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    os.umask(0o077)


def _verify_bundle() -> None:
    root_info = BUNDLE_ROOT.lstat()
    if (not stat.S_ISDIR(root_info.st_mode) or root_info.st_uid != 0
            or root_info.st_gid != 0 or stat.S_IMODE(root_info.st_mode) != 0o755
            or BUNDLE_ROOT.resolve(strict=True) != BUNDLE_ROOT):
        raise WorkerError("Runtime bundle root ownership/mode is invalid.")
    script = Path(sys.argv[0])
    if script != WORKER_PATH or script.resolve(strict=True) != WORKER_PATH:
        raise WorkerError("Runtime worker entrypoint path is not canonical.")
    script_info = script.lstat()
    if (not stat.S_ISREG(script_info.st_mode) or script_info.st_uid != 0
            or script_info.st_gid != 0 or (script_info.st_mode & 0o022)
            or not (script_info.st_mode & stat.S_IXUSR)):
        raise WorkerError("Runtime worker entrypoint is not root-owned and immutable.")


def _verify_output_root(path: Path, *, supervisor_gid: int) -> None:
    try:
        info = path.lstat()
    except OSError as exc:
        raise WorkerError("Runtime output root is unavailable.") from exc
    if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
            or info.st_uid != 0 or info.st_gid != supervisor_gid
            or stat.S_IMODE(info.st_mode) != 0o755
            or path.resolve(strict=True) != path):
        raise WorkerError("Runtime output root ownership/mode is invalid.")


def run_job(job_id: int) -> int:
    runtime_uid = os.getuid()
    runtime_gid = os.getgid()
    api_uid_raw = os.environ.get("ARENA_API_UID", "")
    api_gid_raw = os.environ.get("ARENA_API_GID", "")
    if not api_uid_raw.isdigit() or not api_gid_raw.isdigit():
        raise WorkerError("Supervisor-provided API identity is missing.")
    api_uid, api_gid = int(api_uid_raw), int(api_gid_raw)
    if (api_uid < 1 or api_gid < 1 or api_uid == runtime_uid
            or api_gid == runtime_gid):
        raise WorkerError("Supervisor-provided API identity is invalid.")

    output_root = Path("/run/arena-runtime")
    try:
        supervisor_gid = grp.getgrnam(SUPERVISOR_GROUP).gr_gid
    except KeyError as exc:
        raise WorkerError("Configured supervisor group is unavailable.") from exc
    _verify_output_root(output_root, supervisor_gid=supervisor_gid)

    output_dir = output_root / f"job-{job_id}"
    socket_path = output_dir / "control.sock"
    model_path = output_dir / "model.gguf"
    verify_handoff(
        output_dir, socket_path, runtime_uid=runtime_uid,
        api_gid=api_gid, require_socket=False,
    )
    _verify_readonly_file(model_path, expected_uid=runtime_uid)
    _verify_bundle()
    _verify_binary()

    bridge = UnixRuntimeBridge(
        socket_path, loopback_port=LOOPBACK_PORT, allowed_uid=api_uid,
        runtime_uid=runtime_uid, api_gid=api_gid,
        timeout_seconds=1800.0, client_timeout_seconds=3.0,
        response_timeout_seconds=1800.0, cleanup_socket=False,
    )
    stop_requested = threading.Event()
    signal.signal(signal.SIGTERM, lambda _signum, _frame: stop_requested.set())
    signal.signal(signal.SIGINT, lambda _signum, _frame: stop_requested.set())
    _limits()

    child = None
    bridge_thread = None
    bridge_errors: queue.Queue = queue.Queue(maxsize=1)
    try:
        child = subprocess.Popen(
            _fixed_command(model_path),
            stdin=subprocess.DEVNULL,
            env={
                "PATH": "/usr/bin:/bin",
                "HOME": "/nonexistent",
                "TMPDIR": "/tmp",
                "LANG": "C.UTF-8",
                "OMP_NUM_THREADS": "1",
                "LLAMA_CACHE": "/tmp",
            },
            close_fds=True,
        )

        def serve_bridge():
            try:
                bridge.serve_forever()
            except BaseException as exc:
                bridge_errors.put(exc)

        bridge_thread = threading.Thread(
            target=serve_bridge, name=f"arena-bridge-{job_id}", daemon=True
        )
        bridge_thread.start()
        socket_deadline = time.monotonic() + 10.0
        while not socket_path.exists() and bridge_thread.is_alive():
            if stop_requested.is_set():
                raise WorkerError("Runtime stop requested during bridge startup.")
            if not bridge_errors.empty():
                raise WorkerError("AF_UNIX bridge failed during startup.")
            if time.monotonic() >= socket_deadline:
                raise WorkerError("AF_UNIX bridge socket did not become ready.")
            time.sleep(0.05)

        if not socket_path.exists():
            raise WorkerError("AF_UNIX bridge exited before socket creation.")

        while child.poll() is None and not stop_requested.is_set():
            if not bridge_errors.empty():
                raise WorkerError("AF_UNIX bridge stopped unexpectedly.")
            time.sleep(0.1)

        if stop_requested.is_set() and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)
        return int(child.returncode or 0)
    finally:
        if (bridge_thread is not None and bridge_thread.is_alive()
                and bridge._server is not None):
            bridge.shutdown()
        if bridge_thread is not None:
            bridge_thread.join(timeout=5)
        if child is not None and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)
        # Deliberately do not unlink control.sock. The root supervisor removes
        # it only after systemd confirms the unit cgroup is empty.


def main(argv: list[str] | None = None) -> int:
    try:
        job_id = _parse_job_id(argv)
        return run_job(job_id)
    except (WorkerError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"arena-runtime-worker: {type(exc).__name__}", file=sys.stderr, flush=True)
        return 70


if __name__ == "__main__":
    raise SystemExit(main())
