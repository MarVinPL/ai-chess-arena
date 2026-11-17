import pytest

from runtime_resource_policy import (
    DISK_RESERVE_BYTES,
    HOST_RAM_RESERVE_BYTES,
    MAX_ARTIFACT_BYTES,
    MODEL_MEMORY_RESERVE_BYTES,
    model_memory_limit_bytes,
    required_handoff_free_bytes,
    required_host_available_bytes,
)


def test_host_reserve_is_separate_from_runtime_cgroup_memory_limit():
    size = 1024**3
    model_limit = int(size * 1.5) + MODEL_MEMORY_RESERVE_BYTES
    assert model_memory_limit_bytes(size) == model_limit
    assert required_host_available_bytes(size) == model_limit + HOST_RAM_RESERVE_BYTES


def test_handoff_free_space_includes_artifact_and_reserve():
    size = 512 * 1024**2
    assert required_handoff_free_bytes(size) == size + DISK_RESERVE_BYTES


@pytest.mark.parametrize("size", [0, -1, MAX_ARTIFACT_BYTES + 1, True])
def test_resource_policy_rejects_invalid_artifact_sizes(size):
    with pytest.raises(ValueError):
        model_memory_limit_bytes(size)
    with pytest.raises(ValueError):
        required_host_available_bytes(size)
    with pytest.raises(ValueError):
        required_handoff_free_bytes(size)
