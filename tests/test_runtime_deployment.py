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


def test_test_recovery_profile_isolated_and_execution_disabled(tmp_path):
    root = "/var/tmp/arena-test-profile-example"
    config = resolve_runtime_deployment({
        "ARENA_RUNTIME_DEPLOYMENT": "test-recovery",
        "ARENA_RUNTIME_TEST_ACK": "I_UNDERSTAND_RECOVERY_ONLY_TEST",
        "ARENA_RUNTIME_BACKEND": "systemd",
        "ARENA_RUNTIME_EXECUTION_ENABLED": "0",
        "ARENA_RUNTIME_TEST_ROOT": root,
        "ARENA_RUNTIME_ARTIFACT_ROOT": root + "/data/handoff",
        "ARENA_RUNTIME_OUTPUT_ROOT": root + "/run/output",
        "ARENA_RUNTIME_WORKER_BUNDLE": root + "/bundle",
        "ARENA_RUNTIME_SUPERVISOR_SOCKET": root + "/run/supervisor/supervisor.sock",
        "ARENA_RUNTIME_LEASE_BROKER_SOCKET": root + "/run/api/lease-broker.sock",
        "ARENA_DATABASE_PATH": root + "/state/arena.sqlite3",
        "ARENA_LOG_DIR": root + "/state/logs",
        "ARENA_RUNTIME_EXECUTOR_LOCK_PATH": root + "/run/api/executor.lock",
        "ARENA_HTTP_HOST": "127.0.0.1",
        "ARENA_HTTP_PORT": "0",
        "ARENA_BACKGROUND_JOBS": "0",
    })
    assert config.environment == "test-recovery"
    assert config.supervisor_requested is True
    assert config.production_requested is False
    assert config.execution_enabled is False
    assert config.unit_namespace == "test-recovery"
    assert config.lease_broker_socket == root + "/run/api/lease-broker.sock"


def test_test_recovery_profile_rejects_production_path_overlap(tmp_path):
    root = "/var/tmp/arena-test-profile-example"
    env = {
        "ARENA_RUNTIME_DEPLOYMENT": "test-recovery",
        "ARENA_RUNTIME_TEST_ACK": "I_UNDERSTAND_RECOVERY_ONLY_TEST",
        "ARENA_RUNTIME_BACKEND": "systemd",
        "ARENA_RUNTIME_EXECUTION_ENABLED": "0",
        "ARENA_RUNTIME_TEST_ROOT": root,
        "ARENA_RUNTIME_ARTIFACT_ROOT": "/var/lib/arena-handoff",
        "ARENA_RUNTIME_OUTPUT_ROOT": root + "/run/output",
        "ARENA_RUNTIME_WORKER_BUNDLE": root + "/bundle",
        "ARENA_RUNTIME_SUPERVISOR_SOCKET": root + "/run/supervisor/supervisor.sock",
        "ARENA_RUNTIME_LEASE_BROKER_SOCKET": root + "/run/api/lease-broker.sock",
    }
    with pytest.raises(RuntimeDeploymentConfigError, match="isolated test root"):
        resolve_runtime_deployment(env)


def test_resolver_uses_process_environment_when_no_mapping_is_passed(monkeypatch):
    monkeypatch.setenv("ARENA_RUNTIME_DEPLOYMENT", "staging")
    config = resolve_runtime_deployment()
    assert config.environment == "staging"
    assert config.backend == "inert"
    assert config.execution_enabled is False
