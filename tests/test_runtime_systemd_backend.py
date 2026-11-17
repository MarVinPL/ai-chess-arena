import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from runtime_systemd import build_runtime_unit_spec
from runtime_systemd_backend import SystemdBackendError, SystemdTransientBackend


def make_spec():
    return build_runtime_unit_spec(
        job_id=9, runtime_user="arena-runtime", runtime_group="arena-runtime",
        artifact_path="/var/lib/arena-handoff/job-9/model.gguf",
        artifact_mount_path="/run/arena-runtime/job-9/model.gguf",
        output_dir="/run/arena-runtime/job-9",
        api_uid=os.getuid(), api_gid=os.getgid(), memory_max_bytes=1024**3,
        memory_swap_max_bytes=0, cpu_quota_percent=100, tasks_max=8,
        runtime_seconds=300,
    )


class FakeRunner:
    def __init__(self, show=""):
        self.calls = []
        self.show = show

    def __call__(self, argv, timeout):
        self.calls.append((argv, timeout))
        if argv[0].endswith("systemctl") and "show" in argv:
            output = self.show.split("=", 1)[1] if "--value" in argv and self.show.startswith("ControlGroup=") else self.show
            return SimpleNamespace(returncode=0, stdout=output, stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")


def test_start_uses_only_exact_transient_unit_and_pinned_bundle_worker():
    spec = make_spec()
    expected = SystemdTransientBackend._expected(spec)
    fake = FakeRunner("".join(f"{k}={v}\n" for k, v in expected.items()))
    backend = SystemdTransientBackend(command_runner=fake)
    backend.start(spec, ("/usr/local/lib/ai-chess-arena-runtime/runtime_worker", "--job", "9"))
    command = fake.calls[0][0]
    assert command[:4] == ["/usr/bin/systemd-run", "--system", "--no-block",
                          "--unit=ai-chess-arena-runtime-9.service"]
    assert command[-4:] == ["--", "/usr/local/lib/ai-chess-arena-runtime/runtime_worker", "--job", "9"]
    assert fake.calls[1][0][-1] == "ai-chess-arena-runtime-9.service"


def test_start_fails_closed_when_effective_properties_do_not_match():
    fake = FakeRunner("User=debian\nGroup=debian\n")
    backend = SystemdTransientBackend(command_runner=fake)
    with pytest.raises(SystemdBackendError, match="właściwości"):
        backend.start(make_spec(), ("/usr/local/lib/ai-chess-arena-runtime/runtime_worker", "--job", "9"))


def test_start_rejects_worker_outside_root_owned_bundle_before_command():
    fake = FakeRunner()
    backend = SystemdTransientBackend(command_runner=fake)
    with pytest.raises(SystemdBackendError, match="przypisanemu zadaniu"):
        backend.start(make_spec(), ("/home/debian/worker", "--job", "9"))
    assert fake.calls == []


def test_stop_targets_exact_unit_and_checks_cgroup_empty(tmp_path):
    fake = FakeRunner("ControlGroup=\n")
    backend = SystemdTransientBackend(command_runner=fake, cgroup_root=tmp_path)
    backend.stop("ai-chess-arena-runtime-9.service")
    assert fake.calls[0][0] == ["/usr/bin/systemctl", "stop", "ai-chess-arena-runtime-9.service"]
    assert fake.calls[1][0][-1] == "ai-chess-arena-runtime-9.service"


def test_stop_refuses_non_deterministic_or_unrelated_unit_name():
    fake = FakeRunner()
    backend = SystemdTransientBackend(command_runner=fake)
    with pytest.raises(SystemdBackendError):
        backend.stop("sshd.service")
    assert fake.calls == []


def test_stop_refuses_to_mark_unit_stopped_when_cgroup_still_has_processes(tmp_path):
    unit_cgroup = tmp_path / "system.slice" / "ai-chess-arena-runtime-9.service"
    unit_cgroup.mkdir(parents=True)
    (unit_cgroup / "cgroup.procs").write_text("1234\n", encoding="ascii")
    (unit_cgroup / "cgroup.events").write_text("populated 1\nfrozen 0\n", encoding="ascii")
    fake = FakeRunner("ControlGroup=/system.slice/ai-chess-arena-runtime-9.service\n")
    backend = SystemdTransientBackend(command_runner=fake, cgroup_root=tmp_path)
    with pytest.raises(SystemdBackendError, match="nadal zawiera procesy"):
        backend.stop("ai-chess-arena-runtime-9.service")


def test_stop_refuses_cgroup_with_nested_descendant_even_when_direct_procs_empty(tmp_path):
    unit_cgroup = tmp_path / "system.slice" / "ai-chess-arena-runtime-9.service"
    unit_cgroup.mkdir(parents=True)
    (unit_cgroup / "cgroup.procs").write_text("", encoding="ascii")
    (unit_cgroup / "cgroup.events").write_text("populated 1\nfrozen 0\n", encoding="ascii")
    fake = FakeRunner("ControlGroup=/system.slice/ai-chess-arena-runtime-9.service\n")
    backend = SystemdTransientBackend(command_runner=fake, cgroup_root=tmp_path)
    with pytest.raises(SystemdBackendError, match="nadal zawiera procesy"):
        backend.stop("ai-chess-arena-runtime-9.service")


def test_stop_fails_closed_when_existing_cgroup_lacks_events_file(tmp_path):
    unit_cgroup = tmp_path / "system.slice" / "ai-chess-arena-runtime-9.service"
    unit_cgroup.mkdir(parents=True)
    (unit_cgroup / "cgroup.procs").write_text("", encoding="ascii")
    fake = FakeRunner("ControlGroup=/system.slice/ai-chess-arena-runtime-9.service\n")
    backend = SystemdTransientBackend(command_runner=fake, cgroup_root=tmp_path)
    with pytest.raises(SystemdBackendError, match="Nie można jednoznacznie"):
        backend.stop("ai-chess-arena-runtime-9.service")


def test_stop_refuses_cgroup_path_outside_system_slice(tmp_path):
    fake = FakeRunner("ControlGroup=/user.slice/user-1000.slice/example.service\n")
    backend = SystemdTransientBackend(command_runner=fake, cgroup_root=tmp_path)
    with pytest.raises(SystemdBackendError, match="ścieżka cgroup"):
        backend.stop("ai-chess-arena-runtime-9.service")


def test_systemd_command_failure_is_not_treated_as_success():
    class FailedRunner:
        def __call__(self, argv, timeout):
            return SimpleNamespace(returncode=1, stdout="", stderr="access denied")

    backend = SystemdTransientBackend(command_runner=FailedRunner())
    with pytest.raises(SystemdBackendError, match="zakończyło się błędem"):
        backend.stop("ai-chess-arena-runtime-9.service")


def test_start_rejects_spec_with_weakened_network_or_filesystem_policy():
    from dataclasses import replace

    spec = make_spec()
    props = list(spec.properties)
    props = [(key, "no" if key == "PrivateNetwork" else value) for key, value in props]
    weakened = replace(spec, properties=tuple(props))
    fake = FakeRunner()
    backend = SystemdTransientBackend(command_runner=fake)
    with pytest.raises(SystemdBackendError, match="kompletnej polityki"):
        backend.start(weakened, ("/usr/local/lib/ai-chess-arena-runtime/runtime_worker", "--job", "9"))
    assert fake.calls == []


def test_start_rejects_worker_arguments_not_bound_to_job_id():
    fake = FakeRunner()
    backend = SystemdTransientBackend(command_runner=fake)
    with pytest.raises(SystemdBackendError, match="przypisanemu zadaniu"):
        backend.start(make_spec(), ("/usr/local/lib/ai-chess-arena-runtime/runtime_worker", "--job", "10"))
    assert fake.calls == []
