"""Race-resistant, per-job artifact snapshot for a future privileged supervisor.

The destination root must be provisioned separately. This module creates only
a unique job directory beneath that root; it never creates host-level roots,
accounts, groups, ACLs, sockets, units, or cgroups.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MAX_ARTIFACT_BYTES = 16 * 1024**3
_DISK_RESERVE_BYTES = 512 * 1024**2
_CHUNK_BYTES = 1024 * 1024


class ArtifactHandoffError(RuntimeError):
    """Raised when a pinned artifact cannot be safely handed off."""


@dataclass(frozen=True)
class ArtifactHandoff:
    job_id: int
    path: str
    sha256: str
    size_bytes: int
    owner_uid: int
    runtime_gid: int


def _validate_identity(job_id: int, source_path: str, expected_sha256: str,
                       expected_size: int, runtime_gid: int) -> None:
    if type(job_id) is not int or job_id < 1:
        raise ArtifactHandoffError("Invalid job ID.")
    if not isinstance(source_path, str) or not source_path.startswith("/") or "\x00" in source_path:
        raise ArtifactHandoffError("Artifact source must be an absolute path.")
    parts = PurePosixPath(source_path).parts
    if ".." in parts or len(parts) < 2:
        raise ArtifactHandoffError("Artifact source path is not canonical.")
    if not isinstance(expected_sha256, str) or not _SHA256.fullmatch(expected_sha256):
        raise ArtifactHandoffError("Pinned artifact SHA-256 is invalid.")
    if type(expected_size) is not int or not 1 <= expected_size <= _MAX_ARTIFACT_BYTES:
        raise ArtifactHandoffError("Pinned artifact size is outside policy.")
    if type(runtime_gid) is not int or runtime_gid < 0:
        raise ArtifactHandoffError("Runtime group ID is invalid.")


def _open_source_beneath(source_root: Path, source_path: str) -> int:
    if not source_root.is_absolute() or source_root.resolve(strict=True) != source_root:
        raise ArtifactHandoffError("Quarantine root must be an existing canonical directory.")
    root_fd = os.open(source_root, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        root_stat = os.fstat(root_fd)
        if not stat.S_ISDIR(root_stat.st_mode):
            raise ArtifactHandoffError("Quarantine root is not a directory.")
        try:
            relative = PurePosixPath(source_path).relative_to(PurePosixPath(str(source_root)))
        except ValueError as exc:
            raise ArtifactHandoffError("Artifact source escapes the quarantine root.") from exc
        parts = relative.parts
        if not parts or any(part in {"", ".", ".."} for part in parts):
            raise ArtifactHandoffError("Artifact source escapes the quarantine root.")
        current_fd = root_fd
        owned_fds = []
        try:
            for part in parts[:-1]:
                next_fd = os.open(
                    part, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
                    dir_fd=current_fd,
                )
                owned_fds.append(next_fd)
                current_fd = next_fd
            file_fd = os.open(
                parts[-1], os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
                dir_fd=current_fd,
            )
            file_stat = os.fstat(file_fd)
            if not stat.S_ISREG(file_stat.st_mode):
                os.close(file_fd)
                raise ArtifactHandoffError("Quarantine artifact is not a regular file.")
            return file_fd
        finally:
            for fd in reversed(owned_fds):
                os.close(fd)
    finally:
        os.close(root_fd)


def prepare_artifact_handoff(*, job_id: int, source_root: str | Path,
                             source_path: str, expected_sha256: str,
                             expected_size: int, handoff_root: str | Path,
                             runtime_gid: int, owner_uid: int | None = None) -> ArtifactHandoff:
    """Copy one pinned file into a new root-managed, runtime-readable job directory.

    handoff_root must already be root-owned (or owned by the injected test
    identity), runtime-group-owned, and mode 0710. The API identity must not
    have membership in the runtime group. The function does not create it.
    """
    owner_uid = os.getuid() if owner_uid is None else owner_uid
    if type(owner_uid) is not int or owner_uid < 0:
        raise ArtifactHandoffError("Supervisor owner UID is invalid.")
    source_root = Path(source_root)
    handoff_root = Path(handoff_root)
    _validate_identity(job_id, source_path, expected_sha256, expected_size, runtime_gid)
    if not handoff_root.is_absolute() or handoff_root.resolve(strict=True) != handoff_root:
        raise ArtifactHandoffError("Handoff root must be an existing canonical directory.")

    root_fd = os.open(handoff_root, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW)
    job_fd = None
    source_fd = None
    temp_created = False
    final_created = False
    job_created = False
    success = False
    job_name = f"job-{job_id}"
    try:
        root_stat = os.fstat(root_fd)
        if (not stat.S_ISDIR(root_stat.st_mode) or root_stat.st_uid != owner_uid
                or root_stat.st_gid != runtime_gid
                or stat.S_IMODE(root_stat.st_mode) != 0o710):
            raise ArtifactHandoffError("Handoff root ownership/mode does not match root:runtime 0710 policy.")

        if shutil.disk_usage(handoff_root).free < expected_size + _DISK_RESERVE_BYTES:
            raise ArtifactHandoffError("Handoff volume lacks the required free-space reserve.")
        source_fd = _open_source_beneath(source_root, source_path)
        source_stat = os.fstat(source_fd)
        if source_stat.st_size != expected_size:
            raise ArtifactHandoffError("Quarantine artifact size differs from the pinned registry.")

        os.mkdir(job_name, 0o750, dir_fd=root_fd)
        job_created = True
        job_fd = os.open(
            job_name, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
            dir_fd=root_fd,
        )
        os.fchown(job_fd, owner_uid, runtime_gid)
        os.fchmod(job_fd, 0o750)

        temp_fd = os.open(
            "model.gguf.part",
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
            0o600, dir_fd=job_fd,
        )
        temp_created = True
        digest = hashlib.sha256()
        copied = 0
        try:
            while True:
                block = os.read(source_fd, _CHUNK_BYTES)
                if not block:
                    break
                copied += len(block)
                if copied > expected_size:
                    raise ArtifactHandoffError("Quarantine artifact grew during handoff.")
                digest.update(block)
                view = memoryview(block)
                while view:
                    written = os.write(temp_fd, view)
                    if written <= 0:
                        raise ArtifactHandoffError("Short write while copying artifact.")
                    view = view[written:]
            if copied != expected_size or digest.hexdigest() != expected_sha256:
                raise ArtifactHandoffError("Copied artifact does not match the pinned size/SHA-256.")
            os.fchown(temp_fd, owner_uid, runtime_gid)
            os.fchmod(temp_fd, 0o440)
            os.fsync(temp_fd)
        finally:
            os.close(temp_fd)

        os.rename("model.gguf.part", "model.gguf", src_dir_fd=job_fd, dst_dir_fd=job_fd)
        temp_created = False
        final_created = True
        os.fsync(job_fd)
        result = ArtifactHandoff(
            job_id=job_id,
            path=str(handoff_root / job_name / "model.gguf"),
            sha256=expected_sha256,
            size_bytes=expected_size,
            owner_uid=owner_uid,
            runtime_gid=runtime_gid,
        )
        success = True
        return result
    except ArtifactHandoffError:
        raise
    except OSError as exc:
        raise ArtifactHandoffError("Secure artifact handoff failed.") from exc
    finally:
        if source_fd is not None:
            os.close(source_fd)
        if not success and job_fd is not None:
            if temp_created:
                try:
                    os.unlink("model.gguf.part", dir_fd=job_fd)
                except FileNotFoundError:
                    pass
            if final_created:
                try:
                    os.unlink("model.gguf", dir_fd=job_fd)
                except FileNotFoundError:
                    pass
        if job_fd is not None:
            os.close(job_fd)
        if not success and job_created:
            try:
                os.rmdir(job_name, dir_fd=root_fd)
            except OSError:
                pass
        os.close(root_fd)


def remove_artifact_handoff(*, job_id: int, handoff_root: str | Path,
                             runtime_gid: int, owner_uid: int | None = None) -> bool:
    """Remove only the exact per-job snapshot after its systemd cgroup is empty.

    This function never recurses. Unexpected files, ownership, modes or
    symlinks cause a refusal instead of broad cleanup.
    """
    owner_uid = os.getuid() if owner_uid is None else owner_uid
    if type(job_id) is not int or job_id < 1 or type(runtime_gid) is not int or runtime_gid < 0:
        raise ArtifactHandoffError("Invalid handoff cleanup identity.")
    if type(owner_uid) is not int or owner_uid < 0:
        raise ArtifactHandoffError("Invalid handoff cleanup owner.")
    handoff_root = Path(handoff_root)
    if not handoff_root.is_absolute() or handoff_root.resolve(strict=True) != handoff_root:
        raise ArtifactHandoffError("Handoff root must be an existing canonical directory.")

    root_fd = os.open(handoff_root, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW)
    job_fd = None
    try:
        root_stat = os.fstat(root_fd)
        if (root_stat.st_uid != owner_uid or root_stat.st_gid != runtime_gid
                or stat.S_IMODE(root_stat.st_mode) != 0o710):
            raise ArtifactHandoffError("Handoff root ownership/mode does not match cleanup policy.")
        job_name = f"job-{job_id}"
        try:
            job_fd = os.open(
                job_name, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
                dir_fd=root_fd,
            )
        except FileNotFoundError:
            return False
        job_stat = os.fstat(job_fd)
        if (job_stat.st_uid != owner_uid or job_stat.st_gid != runtime_gid
                or stat.S_IMODE(job_stat.st_mode) != 0o750):
            raise ArtifactHandoffError("Per-job directory ownership/mode does not match cleanup policy.")
        try:
            file_fd = os.open(
                "model.gguf", os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
                dir_fd=job_fd,
            )
        except FileNotFoundError:
            file_fd = None
        if file_fd is not None:
            try:
                file_stat = os.fstat(file_fd)
                if (not stat.S_ISREG(file_stat.st_mode) or file_stat.st_uid != owner_uid
                        or file_stat.st_gid != runtime_gid
                        or stat.S_IMODE(file_stat.st_mode) != 0o440):
                    raise ArtifactHandoffError("Per-job artifact ownership/mode does not match cleanup policy.")
            finally:
                os.close(file_fd)
            os.unlink("model.gguf", dir_fd=job_fd)
        os.close(job_fd)
        job_fd = None
        try:
            os.rmdir(job_name, dir_fd=root_fd)
        except OSError as exc:
            raise ArtifactHandoffError("Per-job directory contains unexpected files; refusing recursive cleanup.") from exc
        os.fsync(root_fd)
        return True
    except ArtifactHandoffError:
        raise
    except OSError as exc:
        raise ArtifactHandoffError("Secure artifact handoff cleanup failed.") from exc
    finally:
        if job_fd is not None:
            os.close(job_fd)
        os.close(root_fd)
