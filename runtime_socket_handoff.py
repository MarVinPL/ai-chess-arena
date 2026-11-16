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
    directory_mode: int = 0o2710
    socket_mode: int = 0o660

    @property
    def directory(self) -> Path:
        return Path(f"/run/arena-runtime/job-{self.job_id}")

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
        if self.directory_mode != 0o2710 or self.socket_mode != 0o660:
            raise SocketHandoffError("Polityka katalogu lub gniazda została osłabiona.")


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
    # Reject symlinks in every path component, not only the final directory.
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
