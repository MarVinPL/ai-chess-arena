"""Fail-closed filesystem contract for the API/runtime AF_UNIX handoff."""
from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path


class SocketHandoffError(RuntimeError):
    pass


@dataclass(frozen=True)
class SocketHandoffSpec:
    job_id: int
    runtime_uid: int
    api_gid: int
    output_root: Path = Path("/run/arena-runtime")
    directory_mode: int = 0o2710
    socket_mode: int = 0o660

    @property
    def directory(self) -> Path:
        return Path(self.output_root) / f"job-{self.job_id}"

    @property
    def socket_path(self) -> Path:
        return self.directory / "control.sock"

    def validate(self) -> None:
        if type(self.job_id) is not int or self.job_id < 1:
            raise SocketHandoffError("Nieprawidłowy identyfikator zadania.")
        if type(self.runtime_uid) is not int or self.runtime_uid < 1:
            raise SocketHandoffError("Nieprawidłowy UID runtime.")
        if type(self.api_gid) is not int or self.api_gid < 1:
            raise SocketHandoffError("Nieprawidłowy GID API.")
        root = Path(self.output_root)
        if not root.is_absolute():
            raise SocketHandoffError("Katalog wyjściowy musi mieć ścieżkę bezwzględną.")
        if self.directory_mode != 0o2710 or self.socket_mode != 0o660:
            raise SocketHandoffError("Polityka katalogu lub gniazda została osłabiona.")


def _verify_output_root(root: Path, *, supervisor_uid: int, supervisor_gid: int) -> int:
    if not root.is_absolute() or root.resolve(strict=True) != root:
        raise SocketHandoffError("Output root must be an existing canonical directory.")
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW)
    info = os.fstat(fd)
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != supervisor_uid
            or info.st_gid != supervisor_gid or stat.S_IMODE(info.st_mode) != 0o755):
        os.close(fd)
        raise SocketHandoffError("Output root owner/group/mode must be supervisor:supervisor 0755.")
    return fd


def verify_handoff(directory: str | Path, socket_path: str | Path, *,
                   runtime_uid: int, api_gid: int,
                   directory_mode: int = 0o2710,
                   socket_mode: int = 0o660,
                   require_socket: bool = True) -> None:
    """Verify exact DAC contract without following symlinks."""
    directory = Path(directory)
    socket_path = Path(socket_path)
    if socket_path.parent != directory or socket_path.name != "control.sock":
        raise SocketHandoffError("Ścieżka gniazda nie odpowiada katalogowi zadania.")
    if not directory.is_absolute():
        raise SocketHandoffError("Katalog handoff musi mieć ścieżkę bezwzględną.")
    current = Path(directory.anchor)
    for component in directory.parts[1:]:
        current = current / component
        try:
            component_stat = current.lstat()
        except FileNotFoundError as exc:
            raise SocketHandoffError("Brak elementu ścieżki handoff.") from exc
        if stat.S_ISLNK(component_stat.st_mode):
            raise SocketHandoffError("Ścieżka handoff zawiera dowiązanie symboliczne.")
    try:
        dst = directory.lstat()
    except FileNotFoundError as exc:
        raise SocketHandoffError("Brak katalogu handoff.") from exc
    if not stat.S_ISDIR(dst.st_mode) or stat.S_ISLNK(dst.st_mode):
        raise SocketHandoffError("Katalog handoff nie jest zwykłym katalogiem.")
    if (dst.st_uid, dst.st_gid, stat.S_IMODE(dst.st_mode)) != (
        runtime_uid, api_gid, directory_mode
    ):
        raise SocketHandoffError("Właściciel, grupa lub tryb katalogu handoff są nieprawidłowe.")
    try:
        sst = socket_path.lstat()
    except FileNotFoundError:
        if require_socket:
            raise SocketHandoffError("Brak gniazda kontrolnego.")
        return
    if not stat.S_ISSOCK(sst.st_mode) or stat.S_ISLNK(sst.st_mode):
        raise SocketHandoffError("Ścieżka kontrolna nie jest gniazdem Unix.")
    if (sst.st_uid, sst.st_gid, stat.S_IMODE(sst.st_mode)) != (
        runtime_uid, api_gid, socket_mode
    ):
        raise SocketHandoffError("Właściciel, grupa lub tryb gniazda są nieprawidłowe.")


def create_handoff(spec: SocketHandoffSpec, *, supervisor_uid: int,
                   supervisor_gid: int) -> None:
    """Create only the per-job directory under an already provisioned output root."""
    spec.validate()
    root_fd = _verify_output_root(
        Path(spec.output_root), supervisor_uid=supervisor_uid,
        supervisor_gid=supervisor_gid,
    )
    name = f"job-{spec.job_id}"
    job_fd = None
    created = False
    try:
        os.mkdir(name, 0o2710, dir_fd=root_fd)
        created = True
        job_fd = os.open(
            name, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
            dir_fd=root_fd,
        )
        os.fchown(job_fd, spec.runtime_uid, spec.api_gid)
        os.fchmod(job_fd, spec.directory_mode)
        # Pre-create the exact BindReadOnlyPaths target. The host-side
        # placeholder is root-owned and unreadable; the unit mount overlays it
        # with the verified runtime-owned source file.
        model_fd = os.open(
            "model.gguf", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
            0, dir_fd=job_fd,
        )
        try:
            os.fchown(model_fd, supervisor_uid, supervisor_gid)
            os.fchmod(model_fd, 0)
            os.fsync(model_fd)
        finally:
            os.close(model_fd)
        os.fsync(job_fd)
        verify_handoff(
            spec.directory, spec.socket_path, runtime_uid=spec.runtime_uid,
            api_gid=spec.api_gid, directory_mode=spec.directory_mode,
            socket_mode=spec.socket_mode, require_socket=False,
        )
        os.fsync(root_fd)
    except Exception:
        if job_fd is not None:
            try:
                try:
                    model_stat = os.stat("model.gguf", dir_fd=job_fd, follow_symlinks=False)
                    if (stat.S_ISREG(model_stat.st_mode) and model_stat.st_size == 0
                            and (model_stat.st_uid, model_stat.st_gid,
                                 stat.S_IMODE(model_stat.st_mode))
                            == (supervisor_uid, supervisor_gid, 0)):
                        os.unlink("model.gguf", dir_fd=job_fd)
                except FileNotFoundError:
                    pass
            finally:
                os.close(job_fd)
                job_fd = None
        if created:
            try:
                os.rmdir(name, dir_fd=root_fd)
            except OSError:
                pass
        raise
    finally:
        if job_fd is not None:
            os.close(job_fd)
        os.close(root_fd)


def cleanup_handoff(spec: SocketHandoffSpec, *, supervisor_uid: int,
                    supervisor_gid: int) -> bool:
    """Remove only the expected control socket and empty per-job output directory."""
    spec.validate()
    root_fd = _verify_output_root(
        Path(spec.output_root), supervisor_uid=supervisor_uid,
        supervisor_gid=supervisor_gid,
    )
    name = f"job-{spec.job_id}"
    job_fd = None
    try:
        try:
            job_fd = os.open(
                name, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
                dir_fd=root_fd,
            )
        except FileNotFoundError:
            return False
        dst = os.fstat(job_fd)
        if (dst.st_uid, dst.st_gid, stat.S_IMODE(dst.st_mode)) != (
            spec.runtime_uid, spec.api_gid, spec.directory_mode
        ):
            raise SocketHandoffError("Katalog zadania ma nieprawidłowego właściciela lub tryb.")
        entries = set(os.listdir(job_fd))
        if entries - {"control.sock", "model.gguf"}:
            raise SocketHandoffError("Katalog zadania zawiera nieoczekiwane pliki.")
        if "model.gguf" in entries:
            model_stat = os.stat("model.gguf", dir_fd=job_fd, follow_symlinks=False)
            if (not stat.S_ISREG(model_stat.st_mode) or model_stat.st_size != 0
                    or (model_stat.st_uid, model_stat.st_gid, stat.S_IMODE(model_stat.st_mode))
                    != (supervisor_uid, supervisor_gid, 0)):
                raise SocketHandoffError("Punkt montowania modelu ma nieprawidłowe atrybuty.")
        if "control.sock" in entries:
            sock_stat = os.stat("control.sock", dir_fd=job_fd, follow_symlinks=False)
            if (not stat.S_ISSOCK(sock_stat.st_mode)
                    or (sock_stat.st_uid, sock_stat.st_gid, stat.S_IMODE(sock_stat.st_mode))
                    != (spec.runtime_uid, spec.api_gid, spec.socket_mode)):
                raise SocketHandoffError("Gniazdo ma nieprawidłowego właściciela lub tryb.")
        # Validate every entry before changing ownership or removing anything.
        # The runtime-owned directory is deliberately not group-writable (02710).
        # The supervisor has CAP_CHOWN but not CAP_DAC_OVERRIDE, so reclaim this
        # exact, verified directory before unlinking its known entries. This keeps
        # the privileged service's capability set narrow while allowing cleanup.
        os.fchown(job_fd, supervisor_uid, supervisor_gid)
        os.fchmod(job_fd, 0o700)
        if "model.gguf" in entries:
            os.unlink("model.gguf", dir_fd=job_fd)
        if "control.sock" in entries:
            os.unlink("control.sock", dir_fd=job_fd)
        os.close(job_fd)
        job_fd = None
        try:
            os.rmdir(name, dir_fd=root_fd)
        except OSError as exc:
            raise SocketHandoffError("Nie można usunąć niepustego katalogu zadania.") from exc
        os.fsync(root_fd)
        return True
    finally:
        if job_fd is not None:
            os.close(job_fd)
        os.close(root_fd)
