import os
import socket
import stat

import pytest

from runtime_socket_handoff import (
    SocketHandoffError, SocketHandoffSpec, verify_handoff,
    create_handoff, cleanup_handoff,
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


def test_verify_rejects_symlink_in_ancestor_component(tmp_path):
    target = make_directory(tmp_path)
    alias = tmp_path / "alias"
    alias.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(SocketHandoffError):
        verify_handoff(alias / target.name, alias / target.name / "control.sock",
                       runtime_uid=os.getuid(), api_gid=os.getgid(),
                       require_socket=False)


def make_output_root(tmp_path):
    root = tmp_path / "output"
    root.mkdir(mode=0o755)
    os.chmod(root, 0o755)
    os.chown(root, os.getuid(), os.getgid())
    return root


def test_create_and_cleanup_job_scoped_output_directory(tmp_path):
    root = make_output_root(tmp_path)
    spec = SocketHandoffSpec(23, os.getuid(), os.getgid(), output_root=root)
    create_handoff(spec, supervisor_uid=os.getuid(), supervisor_gid=os.getgid())
    info = spec.directory.stat()
    assert (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) == (
        os.getuid(), os.getgid(), 0o2710
    )
    verify_handoff(
        spec.directory, spec.socket_path, runtime_uid=os.getuid(),
        api_gid=os.getgid(), require_socket=False,
    )
    assert cleanup_handoff(
        spec, supervisor_uid=os.getuid(), supervisor_gid=os.getgid()
    ) is True
    assert not spec.directory.exists()


def test_cleanup_removes_only_exact_owned_control_socket(tmp_path):
    root = make_output_root(tmp_path)
    spec = SocketHandoffSpec(24, os.getuid(), os.getgid(), output_root=root)
    create_handoff(spec, supervisor_uid=os.getuid(), supervisor_gid=os.getgid())
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        server.bind(str(spec.socket_path))
        os.chmod(spec.socket_path, 0o660)
        os.chown(spec.socket_path, os.getuid(), os.getgid())
        verify_handoff(
            spec.directory, spec.socket_path, runtime_uid=os.getuid(),
            api_gid=os.getgid(),
        )
        assert cleanup_handoff(
            spec, supervisor_uid=os.getuid(), supervisor_gid=os.getgid()
        ) is True
        assert not spec.directory.exists()
    finally:
        server.close()


def test_cleanup_reclaims_verified_runtime_directory_with_chown_capability(tmp_path, monkeypatch):
    root = make_output_root(tmp_path)
    spec = SocketHandoffSpec(27, os.getuid(), os.getgid(), output_root=root)
    create_handoff(spec, supervisor_uid=os.getuid(), supervisor_gid=os.getgid())
    calls = []
    original_fchown = os.fchown
    original_fchmod = os.fchmod

    def track_fchown(fd, uid, gid):
        calls.append(("chown", uid, gid))
        return original_fchown(fd, uid, gid)

    def track_fchmod(fd, mode):
        calls.append(("chmod", mode))
        return original_fchmod(fd, mode)

    monkeypatch.setattr(os, "fchown", track_fchown)
    monkeypatch.setattr(os, "fchmod", track_fchmod)
    assert cleanup_handoff(
        spec, supervisor_uid=os.getuid(), supervisor_gid=os.getgid()
    ) is True
    assert calls == [
        ("chown", os.getuid(), os.getgid()),
        ("chmod", 0o700),
    ]
    assert not spec.directory.exists()


def test_cleanup_refuses_unexpected_output_files(tmp_path):
    root = make_output_root(tmp_path)
    spec = SocketHandoffSpec(25, os.getuid(), os.getgid(), output_root=root)
    create_handoff(spec, supervisor_uid=os.getuid(), supervisor_gid=os.getgid())
    extra = spec.directory / "unexpected"
    extra.write_text("preserve")
    with pytest.raises(SocketHandoffError, match="nieoczekiwane pliki"):
        cleanup_handoff(spec, supervisor_uid=os.getuid(), supervisor_gid=os.getgid())
    assert extra.read_text() == "preserve"


def test_create_refuses_output_root_with_weak_mode(tmp_path):
    root = make_output_root(tmp_path)
    os.chmod(root, 0o777)
    spec = SocketHandoffSpec(26, os.getuid(), os.getgid(), output_root=root)
    with pytest.raises(SocketHandoffError, match="0755"):
        create_handoff(spec, supervisor_uid=os.getuid(), supervisor_gid=os.getgid())
