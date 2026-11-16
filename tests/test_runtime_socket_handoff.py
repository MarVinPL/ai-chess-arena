import os
import socket
import stat

import pytest

from runtime_socket_handoff import (
    SocketHandoffError, SocketHandoffSpec, verify_handoff,
)


def make_directory(tmp_path):
    directory = tmp_path / "job-1"
    directory.mkdir()
    os.chmod(directory, 0o2710)
    return directory


def test_spec_is_fixed_and_job_scoped():
    spec = SocketHandoffSpec(12, 1001, 1002)
    spec.validate()
    assert str(spec.directory) == "/run/arena-runtime/job-12"
    assert str(spec.socket_path) == "/run/arena-runtime/job-12/control.sock"
    with pytest.raises(SocketHandoffError):
        SocketHandoffSpec(12, 1001, 1002, directory_mode=0o2770).validate()


def test_verify_requires_exact_directory_owner_group_and_mode(tmp_path):
    directory = make_directory(tmp_path)
    verify_handoff(directory, directory / "control.sock",
                   runtime_uid=os.getuid(), api_gid=os.getgid(),
                   require_socket=False)
    os.chmod(directory, 0o2770)
    with pytest.raises(SocketHandoffError):
        verify_handoff(directory, directory / "control.sock",
                       runtime_uid=os.getuid(), api_gid=os.getgid(),
                       require_socket=False)


def test_verify_rejects_symlinked_directory(tmp_path):
    target = make_directory(tmp_path)
    link = tmp_path / "job-link"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(SocketHandoffError):
        verify_handoff(link, link / "control.sock",
                       runtime_uid=os.getuid(), api_gid=os.getgid(),
                       require_socket=False)


def test_verify_accepts_socket_with_setgid_inherited_group(tmp_path):
    directory = make_directory(tmp_path)
    path = directory / "control.sock"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        server.bind(str(path))
        os.chmod(path, 0o660)
        info = path.lstat()
        assert stat.S_ISSOCK(info.st_mode)
        assert info.st_gid == os.getgid()
        verify_handoff(directory, path, runtime_uid=os.getuid(), api_gid=os.getgid())
    finally:
        server.close()
        path.unlink(missing_ok=True)


def test_verify_rejects_regular_file_at_socket_path(tmp_path):
    directory = make_directory(tmp_path)
    path = directory / "control.sock"
    path.write_text("not a socket")
    with pytest.raises(SocketHandoffError):
        verify_handoff(directory, path,
                       runtime_uid=os.getuid(), api_gid=os.getgid())
