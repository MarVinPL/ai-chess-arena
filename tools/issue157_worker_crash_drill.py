"""Run the real supervised worker's pre-inference SIGKILL drill inside arena-test.

This script is intentionally container-only. It builds the actual worker bundle,
starts it through RuntimeSupervisor/SystemdTransientBackend in the test-recovery
unit namespace, and injects SIGKILL after the worker's real integrity checks but
before any model process is spawned. No model weights or inference are used.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pwd
import grp
import shutil
import signal
import sqlite3
import stat
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

TEST_ACK = "I_UNDERSTAND_WORKER_CRASH_TEST"
JOB_ID = 910000107
UNIT_NAME = f"ai-chess-arena-runtime-test-{JOB_ID}.service"
BUNDLE_ROOT = Path("/usr/local/lib/ai-chess-arena-runtime")
HANDOFF_ROOT = Path("/var/lib/arena-handoff")
OUTPUT_ROOT = Path("/run/arena-runtime")
MODEL_SOURCE = HANDOFF_ROOT / f"job-{JOB_ID}" / "model.gguf"
OUTPUT_DIR = OUTPUT_ROOT / f"job-{JOB_ID}"
LLAMAFILE_SOURCE = Path("/tmp/aca-llamafile")
LLAMAFILE_SHA256 = "756bf97febae557084e8ced9bde7a1cb0c7f6ce778530d582afa219406237a50"


def run(argv: list[str], *, timeout: int = 30, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(argv, text=True, capture_output=True, timeout=timeout, check=False)
    if check and result.returncode:
        raise RuntimeError(f"command failed ({result.returncode}): {argv[0]}: {(result.stderr or result.stdout)[-1000:]}")
    return result


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def ensure_account() -> tuple[int, int, int, int, bool, bool]:
    created_group = created_user = created_api_group = created_api_user = False
    try:
        api_group = grp.getgrnam("debian")
    except KeyError:
        run(["/usr/sbin/groupadd", "--system", "debian"])
        created_api_group = True
        api_group = grp.getgrnam("debian")
    try:
        runtime_group = grp.getgrnam("arena-runtime")
    except KeyError:
        run(["/usr/sbin/groupadd", "--system", "arena-runtime"])
        created_group = True
        runtime_group = grp.getgrnam("arena-runtime")
    try:
        runtime_user = pwd.getpwnam("arena-runtime")
    except KeyError:
        run(["/usr/sbin/useradd", "--system", "--gid", "arena-runtime", "--no-create-home",
             "--home-dir", "/nonexistent", "--shell", "/usr/sbin/nologin", "arena-runtime"])
        created_user = True
        runtime_user = pwd.getpwnam("arena-runtime")
    try:
        api_user = pwd.getpwnam("arena-api-test")
    except KeyError:
        run(["/usr/sbin/useradd", "--system", "--gid", "debian", "--no-create-home",
             "--home-dir", "/nonexistent", "--shell", "/usr/sbin/nologin", "arena-api-test"])
        created_api_user = True
        api_user = pwd.getpwnam("arena-api-test")
    if runtime_user.pw_uid == 0 or api_user.pw_uid == 0 or runtime_user.pw_uid == api_user.pw_uid:
        raise RuntimeError("test identities are not distinct unprivileged users")
    if runtime_group.gr_gid == api_group.gr_gid:
        raise RuntimeError("runtime and API groups must be distinct")
    return (runtime_user.pw_uid, runtime_group.gr_gid, api_user.pw_uid, api_group.gr_gid,
            created_group or created_user, created_api_group or created_api_user)


def remove_test_accounts(created_runtime: bool, created_api: bool) -> None:
    if created_api:
        run(["/usr/sbin/userdel", "arena-api-test"], check=False)
        run(["/usr/sbin/groupdel", "debian"], check=False)
    if created_runtime:
        run(["/usr/sbin/userdel", "arena-runtime"], check=False)
        run(["/usr/sbin/groupdel", "arena-runtime"], check=False)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ack", required=True)
    parser.add_argument("--source-root", type=Path, default=Path("/tmp/aca-worker-crash-src"))
    parser.add_argument("--test-root", type=Path, default=Path("/var/tmp/aca-worker-crash-test-20261118"))
    args = parser.parse_args()
    if args.ack != TEST_ACK:
        raise SystemExit("FAIL: exact worker-crash test acknowledgement required")
    if os.geteuid() != 0 or run(["/usr/bin/systemd-detect-virt", "--container"], check=False).stdout.strip() != "systemd-nspawn":
        raise SystemExit("FAIL: this drill may run only as root inside systemd-nspawn")
    if not Path("/run/systemd/system").exists():
        raise SystemExit("FAIL: systemd manager is not running in this container")
    if args.test_root.parent != Path("/var/tmp") or args.test_root.exists():
        raise SystemExit("FAIL: test root must be a new dedicated direct child of /var/tmp")
    if not args.source_root.is_dir() or LLAMAFILE_SOURCE.is_symlink() or not LLAMAFILE_SOURCE.is_file():
        raise SystemExit("FAIL: required staged source files are unavailable")
    if digest(LLAMAFILE_SOURCE) != LLAMAFILE_SHA256:
        raise SystemExit("FAIL: staged llamafile digest mismatch")

    created_runtime = created_api = False
    unit_started = False
    report: dict = {"profile": "test-recovery/test-worker-crash", "worker": {}, "supervisor": {}, "cleanup": {}}
    try:
        runtime_uid, runtime_gid, api_uid, api_gid, created_runtime, created_api = ensure_account()
        args.test_root.mkdir(mode=0o700)
        db_path = args.test_root / "arena.sqlite3"
        with sqlite3.connect(db_path) as connection:
            connection.execute("CREATE TABLE runtime_test_jobs (id INTEGER PRIMARY KEY, status TEXT NOT NULL, cancel_requested INTEGER NOT NULL)")
            connection.execute("INSERT INTO runtime_test_jobs(id,status,cancel_requested) VALUES(?,?,0)", (JOB_ID, "running"))
            connection.commit()
        os.chown(db_path, 0, 0)
        os.chmod(db_path, 0o600)

        BUNDLE_ROOT.mkdir(mode=0o755, parents=True, exist_ok=False)
        os.chown(BUNDLE_ROOT, 0, 0)
        os.chmod(BUNDLE_ROOT, 0o755)
        run([sys.executable, str(args.source_root / "tools/build_runtime_worker_bundle.py"),
             "--repo-root", str(args.source_root), "--output", str(BUNDLE_ROOT / "runtime_worker")])
        os.chown(BUNDLE_ROOT / "runtime_worker", 0, 0)
        os.chmod(BUNDLE_ROOT / "runtime_worker", 0o555)
        shutil.copyfile(LLAMAFILE_SOURCE, BUNDLE_ROOT / "llamafile")
        os.chown(BUNDLE_ROOT / "llamafile", 0, 0)
        os.chmod(BUNDLE_ROOT / "llamafile", 0o555)
        report["worker"]["bundle_sha256"] = digest(BUNDLE_ROOT / "runtime_worker")
        report["worker"]["llamafile_sha256"] = digest(BUNDLE_ROOT / "llamafile")

        HANDOFF_ROOT.mkdir(mode=0o711, parents=True, exist_ok=True)
        os.chown(HANDOFF_ROOT, 0, 0)
        os.chmod(HANDOFF_ROOT, 0o711)
        model_dir = MODEL_SOURCE.parent
        model_dir.mkdir(mode=0o711, parents=True, exist_ok=False)
        os.chown(model_dir, 0, 0)
        os.chmod(model_dir, 0o711)
        MODEL_SOURCE.write_bytes(b"test-only-placeholder-not-a-model")
        os.chown(MODEL_SOURCE, runtime_uid, runtime_gid)
        os.chmod(MODEL_SOURCE, 0o400)

        OUTPUT_ROOT.mkdir(mode=0o755, parents=True, exist_ok=True)
        os.chown(OUTPUT_ROOT, 0, api_gid)
        os.chmod(OUTPUT_ROOT, 0o755)
        OUTPUT_DIR.mkdir(mode=0o2710)
        os.chown(OUTPUT_DIR, runtime_uid, api_gid)
        os.chmod(OUTPUT_DIR, 0o2710)
        mount_target = OUTPUT_DIR / "model.gguf"
        mount_target.touch()
        os.chown(mount_target, 0, 0)
        os.chmod(mount_target, 0o000)

        sys.path.insert(0, str(args.source_root))
        from runtime_supervisor import RuntimeSupervisor, SQLiteRuntimeUnitStore
        from runtime_systemd import build_runtime_unit_spec
        from runtime_systemd_backend import SystemdTransientBackend

        store = SQLiteRuntimeUnitStore(db_path, initialize_schema=True, unit_namespace="test-recovery")
        backend = SystemdTransientBackend(unit_namespace="test-recovery")
        spec = build_runtime_unit_spec(
            job_id=JOB_ID, runtime_user="arena-runtime", runtime_group="arena-runtime",
            artifact_path=str(MODEL_SOURCE), artifact_mount_path=str(mount_target),
            output_dir=str(OUTPUT_DIR), api_uid=api_uid, api_gid=api_gid,
            memory_max_bytes=512 * 1024 * 1024, memory_swap_max_bytes=0,
            cpu_quota_percent=100, tasks_max=8, runtime_seconds=60,
            test_worker_crash=True,
        )
        spec = replace(spec, unit_name=UNIT_NAME)
        argv = (str(BUNDLE_ROOT / "runtime_worker"), "--job", str(JOB_ID))
        starter = RuntimeSupervisor(
            backend=backend, persist=store.persist, set_state=store.set_state,
            load_recoverable=store.load_recoverable, unit_namespace="test-recovery",
        )
        unit_started = True
        start_error = None
        try:
            starter.start(spec, argv)
            report["worker"]["start_result"] = "systemd accepted the real worker process"
        except Exception as exc:
            start_error = exc
            report["worker"]["start_result"] = "backend observed worker exit before active acknowledgement"
            report["worker"]["backend_error_type"] = type(exc).__name__

        deadline = time.monotonic() + 30
        properties = {}
        while time.monotonic() < deadline:
            show = run(["/usr/bin/systemctl", "show", "--no-pager", "--property=LoadState,ActiveState,Result,ExecMainCode,ExecMainStatus,ControlGroup", UNIT_NAME])
            properties = dict(line.split("=", 1) for line in show.stdout.splitlines() if "=" in line)
            if properties.get("ExecMainStatus") == str(signal.SIGKILL) and properties.get("ActiveState") in {"failed", "inactive"}:
                break
            time.sleep(0.1)
        report["worker"]["systemd"] = properties
        if properties.get("ExecMainStatus") != str(signal.SIGKILL):
            raise RuntimeError(f"worker main process was not confirmed SIGKILLed; start_error={start_error!r}; state={properties}")
        if properties.get("ActiveState") not in {"failed", "inactive"}:
            raise RuntimeError(f"worker unit did not reach a terminal state: {properties}")

        lease = store.get(JOB_ID)
        if lease is None or lease.unit_name != UNIT_NAME or lease.state not in {"active", "start_uncertain"}:
            raise RuntimeError(f"worker crash did not leave exact recoverable lease: {lease!r}")
        report["supervisor"]["lease_after_crash"] = lease.state

        # Simulate supervisor process restart: new object, same durable SQLite lease.
        recovered_supervisor = RuntimeSupervisor(
            backend=SystemdTransientBackend(unit_namespace="test-recovery"),
            persist=store.persist, set_state=store.set_state,
            load_recoverable=store.load_recoverable, unit_namespace="test-recovery",
        )
        recovered = recovered_supervisor.recover()
        final_lease = store.get(JOB_ID)
        if recovered != {"stopped_units": [UNIT_NAME], "count": 1}:
            raise RuntimeError(f"supervisor did not recover the exact worker unit: {recovered}")
        if final_lease is None or final_lease.state != "stopped":
            raise RuntimeError(f"worker crash lease did not reach stopped: {final_lease!r}")
        report["supervisor"]["recovery"] = recovered
        report["supervisor"]["lease_after_recovery"] = final_lease.state
        report["supervisor"]["cgroup_empty"] = recovered_supervisor._backend._cgroup_is_empty(UNIT_NAME)
        if not report["supervisor"]["cgroup_empty"]:
            raise RuntimeError("worker unit cgroup still contains processes")
        report["worker"]["model_process_spawned"] = False
        report["worker"]["inference_executed"] = False
        report["worker"]["placeholder_model_bytes"] = MODEL_SOURCE.stat().st_size
        report["worker"]["worker_process_exit"] = "SIGKILL"
        return_code = 0
    finally:
        if unit_started:
            run(["/usr/bin/systemctl", "stop", UNIT_NAME], check=False)
            run(["/usr/bin/systemctl", "reset-failed", UNIT_NAME], check=False)
        shutil.rmtree(OUTPUT_DIR, ignore_errors=True)
        try:
            OUTPUT_ROOT.rmdir()
        except OSError:
            pass
        shutil.rmtree(MODEL_SOURCE.parent, ignore_errors=True)
        try:
            HANDOFF_ROOT.rmdir()
        except OSError:
            pass
        shutil.rmtree(BUNDLE_ROOT, ignore_errors=True)
        shutil.rmtree(args.test_root, ignore_errors=True)
        remove_test_accounts(created_runtime, created_api)
        report["cleanup"]["test_paths_removed"] = not any(
            path.exists() for path in (BUNDLE_ROOT, MODEL_SOURCE.parent, OUTPUT_DIR, args.test_root)
        )
        report["cleanup"]["test_accounts_removed"] = (
            (not created_runtime or (not _exists_user("arena-runtime") and not _exists_group("arena-runtime")))
            and (not created_api or (not _exists_user("arena-api-test") and not _exists_group("debian")))
        )
        report["cleanup"]["container_only"] = True
    print(json.dumps(report, sort_keys=True, indent=2))
    return 0


def _exists_user(name: str) -> bool:
    try:
        pwd.getpwnam(name)
        return True
    except KeyError:
        return False


def _exists_group(name: str) -> bool:
    try:
        grp.getgrnam(name)
        return True
    except KeyError:
        return False


if __name__ == "__main__":
    raise SystemExit(main())
