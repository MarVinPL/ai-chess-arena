import pytest

from runtime_deployment import (
    RuntimeDeploymentConfigError,
    resolve_runtime_deployment,
)


def test_default_is_disabled_and_inert():
    config = resolve_runtime_deployment({})
    assert config.environment == "disabled"
    assert config.backend == "inert"
    assert config.execution_enabled is False
    assert config.supervisor_socket is None


def test_staging_is_inert_even_if_global_execution_flag_is_set():
    config = resolve_runtime_deployment({
        "ARENA_RUNTIME_DEPLOYMENT": "staging",
        "ARENA_RUNTIME_EXECUTION_ENABLED": "1",
        "ARENA_RUNTIME_BACKEND": "systemd",
    })
    assert config.environment == "staging"
    assert config.backend == "inert"
    assert config.execution_enabled is False


def test_production_requires_explicit_acknowledgement():
    with pytest.raises(RuntimeDeploymentConfigError, match="PRODUCTION_ACK"):
        resolve_runtime_deployment({"ARENA_RUNTIME_DEPLOYMENT": "production"})


def test_production_requires_systemd_and_explicit_execution_opt_in():
    base = {
        "ARENA_RUNTIME_DEPLOYMENT": "production",
        "ARENA_RUNTIME_PRODUCTION_ACK": "I_UNDERSTAND_RUNTIME_EXECUTION",
        "ARENA_RUNTIME_BACKEND": "systemd",
        "ARENA_RUNTIME_SUPERVISOR_SOCKET": "/run/ai-chess-arena-runtime/supervisor.sock",
        "ARENA_RUNTIME_LEASE_BROKER_SOCKET": "/run/ai-chess-arena-api/lease-broker.sock",
    }
    with pytest.raises(RuntimeDeploymentConfigError, match="EXECUTION_ENABLED"):
        resolve_runtime_deployment(base)


def test_production_resolves_to_one_canonical_contract():
    config = resolve_runtime_deployment({
        "ARENA_RUNTIME_DEPLOYMENT": "production",
        "ARENA_RUNTIME_PRODUCTION_ACK": "I_UNDERSTAND_RUNTIME_EXECUTION",
        "ARENA_RUNTIME_BACKEND": "systemd",
        "ARENA_RUNTIME_EXECUTION_ENABLED": "1",
        "ARENA_RUNTIME_SUPERVISOR_SOCKET": "/run/ai-chess-arena-runtime/supervisor.sock",
        "ARENA_RUNTIME_LEASE_BROKER_SOCKET": "/run/ai-chess-arena-api/lease-broker.sock",
    })
    assert config.environment == "production"
    assert config.lease_broker_socket == "/run/ai-chess-arena-api/lease-broker.sock"
    assert config.backend == "systemd"
    assert config.execution_enabled is True
    assert config.artifact_root == "/var/lib/arena-handoff"
    assert config.output_root == "/run/arena-runtime"
    assert config.worker_bundle == "/usr/local/lib/ai-chess-arena-runtime"


@pytest.mark.parametrize("key,value", [
    ("ARENA_RUNTIME_SUPERVISOR_SOCKET", "/tmp/runtime.sock"),
    ("ARENA_RUNTIME_ARTIFACT_ROOT", "/var/lib/ai-chess-arena"),
    ("ARENA_RUNTIME_OUTPUT_ROOT", "/tmp/runtime"),
    ("ARENA_RUNTIME_WORKER_BUNDLE", "/home/debian/runtime"),
    ("ARENA_RUNTIME_SUPERVISOR_SOCKET", "/run/../tmp/runtime.sock"),
])
def test_production_rejects_noncanonical_paths(key, value):
    env = {
        "ARENA_RUNTIME_DEPLOYMENT": "production",
        "ARENA_RUNTIME_PRODUCTION_ACK": "I_UNDERSTAND_RUNTIME_EXECUTION",
        "ARENA_RUNTIME_BACKEND": "systemd",
        "ARENA_RUNTIME_EXECUTION_ENABLED": "1",
        "ARENA_RUNTIME_SUPERVISOR_SOCKET": "/run/ai-chess-arena-runtime/supervisor.sock",
        "ARENA_RUNTIME_LEASE_BROKER_SOCKET": "/run/ai-chess-arena-api/lease-broker.sock",
    }
    env[key] = value
    with pytest.raises(RuntimeDeploymentConfigError):
        resolve_runtime_deployment(env)


def test_unknown_mode_fails_closed():
    with pytest.raises(RuntimeDeploymentConfigError, match="dozwolone"):
        resolve_runtime_deployment({"ARENA_RUNTIME_DEPLOYMENT": "prod"})


def test_production_requires_canonical_lease_broker_socket():
    env = {
        "ARENA_RUNTIME_DEPLOYMENT": "production",
        "ARENA_RUNTIME_PRODUCTION_ACK": "I_UNDERSTAND_RUNTIME_EXECUTION",
        "ARENA_RUNTIME_BACKEND": "systemd",
        "ARENA_RUNTIME_EXECUTION_ENABLED": "1",
        "ARENA_RUNTIME_SUPERVISOR_SOCKET": "/run/ai-chess-arena-runtime/supervisor.sock",
        "ARENA_RUNTIME_LEASE_BROKER_SOCKET": "/tmp/lease.sock",
    }
    with pytest.raises(RuntimeDeploymentConfigError, match="Broker dzierżaw"):
        resolve_runtime_deployment(env)
