"""Single source of truth for conservative AI Chess Arena runtime budgets."""

MAX_ARTIFACT_BYTES = 16 * 1024**3
MEMORY_MULTIPLIER = 1.5
MODEL_MEMORY_RESERVE_BYTES = 1536 * 1024**2
HOST_RAM_RESERVE_BYTES = 1536 * 1024**2
DISK_RESERVE_BYTES = 512 * 1024**2


def _validated_size(size_bytes: int) -> int:
    if type(size_bytes) is not int or not 1 <= size_bytes <= MAX_ARTIFACT_BYTES:
        raise ValueError("Artifact size is outside runtime policy.")
    return size_bytes


def model_memory_limit_bytes(size_bytes: int) -> int:
    """MemoryMax for the isolated model cgroup, excluding the host reserve."""
    size_bytes = _validated_size(size_bytes)
    return int(size_bytes * MEMORY_MULTIPLIER) + MODEL_MEMORY_RESERVE_BYTES


def required_host_available_bytes(size_bytes: int) -> int:
    """Minimum MemAvailable before start: model budget plus API/DB/OS reserve."""
    return model_memory_limit_bytes(size_bytes) + HOST_RAM_RESERVE_BYTES


def required_handoff_free_bytes(size_bytes: int) -> int:
    """Minimum free bytes on the handoff volume before copying one artifact."""
    return _validated_size(size_bytes) + DISK_RESERVE_BYTES
