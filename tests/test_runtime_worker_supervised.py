import hashlib
import os
import stat

import pytest

import runtime_worker_supervised as worker
from runtime_supervisor_handlers import RuntimeSupervisorHandlerError, RuntimeSupervisorHandlers


def test_worker_accepts_only_job_id_argument():
    assert worker._parse_job_id(["--job", "31"]) == 31
    with pytest.raises((SystemExit, worker.WorkerError)):
        worker._parse_job_id(["--job", "31", "--model", "/tmp/other.gguf"])
    with pytest.raises((SystemExit, worker.WorkerError)):
        worker._parse_job_id(["--job", "0"])


def test_worker_readonly_model_file_checks_owner_mode_size_and_hash(tmp_path):
    model = tmp_path / "model.gguf"
    content = b"pinned model snapshot"
    model.write_bytes(content)
    os.chmod(model, 0o400)
    digest, size = worker._verify_readonly_file(model, expected_uid=os.getuid())
    assert digest == hashlib.sha256(content).hexdigest()
    assert size == len(content)


@pytest.mark.parametrize("mode", [0o440, 0o600, 0o444, 0o000])
def test_worker_refuses_model_file_with_wrong_mode(tmp_path, mode):
    model = tmp_path / "model.gguf"
    model.write_bytes(b"not accepted")
    os.chmod(model, mode)
    with pytest.raises((worker.WorkerError, OSError)):
        worker._verify_readonly_file(model, expected_uid=os.getuid())


def test_worker_refuses_symlink_model_file(tmp_path):
    target = tmp_path / "target.gguf"
    target.write_bytes(b"model")
    os.chmod(target, 0o400)
    link = tmp_path / "model.gguf"
    link.symlink_to(target)
    with pytest.raises(OSError):
        worker._verify_readonly_file(link, expected_uid=os.getuid())


def test_worker_command_is_fixed_to_loopback_and_single_thread(tmp_path, monkeypatch):
    binary = tmp_path / "llamafile"
    binary.write_bytes(b"\x7fELFfake")
    monkeypatch.setattr(worker, "LLAMAFILE", binary)
    model = tmp_path / "model.gguf"
    command = worker._fixed_command(model)
    assert command == [
        str(binary), "-m", str(model), "--server", "--host", "127.0.0.1",
        "--port", "18080", "--threads", "1", "--ctx-size", "2048",
        "--parallel", "1", "--temp", "0", "--no-ui", "--no-warmup",
    ]


def test_worker_uses_shell_polyglot_only_for_the_pinned_binary_path(tmp_path, monkeypatch):
    binary = tmp_path / "llamafile"
    binary.write_text("#!/bin/sh\nexit 0\n")
    monkeypatch.setattr(worker, "LLAMAFILE", binary)
    command = worker._fixed_command(tmp_path / "model.gguf")
    assert command[:2] == ["/bin/sh", str(binary)]


def test_worker_accepts_only_root_owned_pinned_executable(tmp_path, monkeypatch):
    from types import SimpleNamespace

    binary = tmp_path / "llamafile"
    content = b"#!/bin/sh\nexit 0\n"
    binary.write_bytes(content)
    monkeypatch.setattr(worker, "LLAMAFILE", binary)
    monkeypatch.setattr(worker, "LLAMAFILE_SHA256", hashlib.sha256(content).hexdigest())
    real_fstat = worker.os.fstat
    monkeypatch.setattr(
        worker.os, "fstat",
        lambda fd: SimpleNamespace(
            st_mode=stat.S_IFREG | 0o755, st_uid=0,
            st_size=real_fstat(fd).st_size,
        ),
    )

    worker._verify_binary()


def test_worker_rejects_pinned_binary_hash_mismatch(tmp_path, monkeypatch):
    from types import SimpleNamespace

    binary = tmp_path / "llamafile"
    binary.write_bytes(b"#!/bin/sh\nexit 0\n")
    monkeypatch.setattr(worker, "LLAMAFILE", binary)
    monkeypatch.setattr(worker, "LLAMAFILE_SHA256", "0" * 64)
    real_fstat = worker.os.fstat
    monkeypatch.setattr(
        worker.os, "fstat",
        lambda fd: SimpleNamespace(
            st_mode=stat.S_IFREG | 0o755, st_uid=0,
            st_size=real_fstat(fd).st_size,
        ),
    )

    with pytest.raises(worker.WorkerError, match="SHA-256 mismatch"):
        worker._verify_binary()


def test_worker_rejects_group_or_world_writable_pinned_binary(tmp_path, monkeypatch):
    from types import SimpleNamespace

    binary = tmp_path / "llamafile"
    content = b"#!/bin/sh\nexit 0\n"
    binary.write_bytes(content)
    monkeypatch.setattr(worker, "LLAMAFILE", binary)
    monkeypatch.setattr(worker, "LLAMAFILE_SHA256", hashlib.sha256(content).hexdigest())
    real_fstat = worker.os.fstat
    monkeypatch.setattr(
        worker.os, "fstat",
        lambda fd: SimpleNamespace(
            st_mode=stat.S_IFREG | 0o775, st_uid=0,
            st_size=real_fstat(fd).st_size,
        ),
    )

    with pytest.raises(worker.WorkerError, match="permissions are invalid"):
        worker._verify_binary()


def test_worker_output_root_matches_supervisor_group_policy(tmp_path, monkeypatch):
    from types import SimpleNamespace

    output = tmp_path / "runtime-output"
    output.mkdir()
    os.chmod(output, 0o755)
    real_lstat = worker.Path.lstat
    monkeypatch.setattr(worker.Path, "lstat", lambda path: SimpleNamespace(
        st_mode=stat.S_IFDIR | 0o755, st_uid=0, st_gid=1000,
    ) if path == output else real_lstat(path))
    worker._verify_output_root(output, supervisor_gid=1000)
    with pytest.raises(worker.WorkerError, match="ownership/mode"):
        worker._verify_output_root(output, supervisor_gid=0)


def test_worker_bundle_requires_fixed_root_owned_executable_path(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(worker, "BUNDLE_ROOT", __import__("pathlib").Path("/tmp/not-installed"))
    monkeypatch.setattr(worker, "WORKER_PATH", __import__("pathlib").Path("/tmp/not-installed/runtime_worker"))
    monkeypatch.setattr(worker.sys, "argv", ["/tmp/not-installed/runtime_worker"])
    monkeypatch.setattr(worker.Path, "resolve", lambda self, strict=False: self)
    # Root-owned path verification proceeds to the entrypoint stat; no host path is opened.
    calls = {"count": 0}
    def fake_lstat(self):
        calls["count"] += 1
        if calls["count"] == 1:
            return SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_uid=0, st_gid=0)
        return SimpleNamespace(st_mode=stat.S_IFREG | 0o755, st_uid=0, st_gid=0)
    monkeypatch.setattr(worker.Path, "lstat", fake_lstat)
    worker._verify_bundle()


def test_worker_bundle_builder_is_deterministic_and_executable(tmp_path):
    import subprocess
    import sys
    import zipfile
    from tools.build_runtime_worker_bundle import WORKER_MODULES, build_worker_bundle

    first = tmp_path / "runtime_worker"
    second = tmp_path / "runtime_worker.second"
    digest1 = build_worker_bundle(__import__("pathlib").Path(__file__).resolve().parents[1], first)
    digest2 = build_worker_bundle(__import__("pathlib").Path(__file__).resolve().parents[1], second)
    assert digest1 == digest2
    assert first.read_bytes() == second.read_bytes()
    assert first.read_bytes().startswith(b"#!/usr/bin/python3\n")
    assert first.stat().st_mode & stat.S_IXUSR
    with zipfile.ZipFile(first) as archive:
        assert set(archive.namelist()) == {"__main__.py", *WORKER_MODULES}
        assert archive.testzip() is None
    result = subprocess.run([str(first)], text=True, capture_output=True, check=False)
    assert result.returncode == 2
    assert "--job" in result.stderr
