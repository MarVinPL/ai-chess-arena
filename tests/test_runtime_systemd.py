import os

import pytest

from runtime_systemd import RuntimeUnitPolicyError, build_runtime_unit_spec


def build(**overrides):
    values = {
        "job_id": 42,
        "runtime_user": "arena-runtime",
        "runtime_group": "arena-runtime",
        "artifact_path": "/var/lib/arena-handoff/job-42/model.gguf",
        "artifact_mount_path": "/run/arena-runtime/job-42/model.gguf",
        "output_dir": "/run/arena-runtime/job-42",
        "api_uid": os.getuid(), "api_gid": os.getgid(),
        "memory_max_bytes": 1024 * 1024 * 1024,
        "memory_swap_max_bytes": 0,
        "cpu_quota_percent": 100,
        "tasks_max": 8,
        "runtime_seconds": 300,
    }
    values.update(overrides)
    if "artifact_path" not in overrides:
        values["artifact_path"] = f"/var/lib/arena-handoff/job-{values['job_id']}/model.gguf"
    if "output_dir" not in overrides:
        values["output_dir"] = f"/run/arena-runtime/job-{values['job_id']}"
    if "artifact_mount_path" not in overrides:
        values["artifact_mount_path"] = f"/run/arena-runtime/job-{values['job_id']}/model.gguf"
    return build_runtime_unit_spec(**values)


def test_unit_name_is_stable_per_job_and_does_not_include_user_input():
    assert build().unit_name == "ai-chess-arena-runtime-42.service"
    assert build(job_id=43).unit_name == "ai-chess-arena-runtime-43.service"


def test_policy_requires_separate_unprivileged_identity_and_strict_isolation():
    props = build().as_dict()
    assert props["User"] == "arena-runtime"
    assert props["Group"] == "arena-runtime"
    assert props["NoNewPrivileges"] == "yes"
    assert props["CapabilityBoundingSet"] == ""
    assert props["ProtectSystem"] == "strict"
    assert props["ProtectHome"] == "yes"
    assert props["PrivateNetwork"] == "yes"
    assert props["RestrictAddressFamilies"] == "AF_UNIX AF_INET"
    assert props["KillMode"] == "control-group"
    assert props["Delegate"] == "no"


def test_policy_mounts_only_one_read_only_artifact_and_private_output():
    props = build().as_dict()
    assert props["ReadOnlyPaths"] == "/usr/local/lib/ai-chess-arena-runtime"
    assert props["BindReadOnlyPaths"] == (
        "/var/lib/arena-handoff/job-42/model.gguf:/run/arena-runtime/job-42/model.gguf"
    )
    assert "/var/lib/arena-handoff" in props["InaccessiblePaths"].split()
    assert props["ReadWritePaths"] == "/run/arena-runtime/job-42"
    assert props["WorkingDirectory"] == "/run/arena-runtime/job-42"
    assert "/var/lib/ai-chess-arena" in props["InaccessiblePaths"]
    assert "/etc/ai-chess-arena" in props["InaccessiblePaths"]
    assert "/home" in props["InaccessiblePaths"]


@pytest.mark.parametrize("field,value", [
    ("job_id", 0), ("job_id", True),
    ("runtime_user", "debian"), ("runtime_user", "root;id"),
    ("runtime_group", "sudo"), ("artifact_path", "relative/model.gguf"),
    ("artifact_path", "/var/lib/arena/../secret.gguf"),
    ("output_dir", "/var/lib/arena-handoff/job-42"),
    ("output_dir", "/run/arena-runtime/job-43"),
    ("artifact_path", "/var/lib/arena-handoff/job-43/model.gguf"),
    ("memory_max_bytes", 1), ("memory_swap_max_bytes", -1),
    ("cpu_quota_percent", 201), ("tasks_max", 0),
    ("runtime_seconds", 1801),
])
def test_invalid_or_unsafe_unit_parameters_fail_closed(field, value):
    with pytest.raises(RuntimeUnitPolicyError):
        build(**{field: value})


def test_runtime_policy_does_not_expose_host_tcp_network():
    props = build().as_dict()
    assert props["PrivateNetwork"] == "yes"
    assert props["RestrictAddressFamilies"] == "AF_UNIX AF_INET"
    # IPv4 is permitted only within the PrivateNetwork namespace; the host/API
    # control channel is AF_UNIX and is separately peer-credential checked.
