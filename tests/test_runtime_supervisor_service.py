import os
import pytest

from runtime_supervisor_service import (
    SupervisorServiceConfigError,
    _absolute,
    _required,
    build_service,
)


def test_required_rejects_missing_and_whitespace_values():
    with pytest.raises(SupervisorServiceConfigError):
        _required({}, "ARENA_RUNTIME_DATABASE")
    with pytest.raises(SupervisorServiceConfigError):
        _required({"ARENA_RUNTIME_DATABASE": "  "}, "ARENA_RUNTIME_DATABASE")


def test_absolute_rejects_relative_and_parent_traversal():
    with pytest.raises(SupervisorServiceConfigError):
        _absolute({"P": "relative/db.sqlite"}, "P")
    with pytest.raises(SupervisorServiceConfigError):
        _absolute({"P": "/var/lib/../etc/passwd"}, "P")


def test_service_refuses_non_production_profile_before_host_access(monkeypatch):
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    with pytest.raises(SupervisorServiceConfigError, match="production deployment"):
        build_service({})


def test_service_refuses_missing_production_acknowledgement(monkeypatch):
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    env = {"ARENA_RUNTIME_DEPLOYMENT": "production"}
    with pytest.raises(SupervisorServiceConfigError, match="acknowledgement"):
        build_service(env)


def test_service_refuses_non_root_before_reading_configuration(monkeypatch):
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    with pytest.raises(SupervisorServiceConfigError, match="must run as root"):
        build_service({})



def test_service_refuses_noncanonical_supervisor_bundle_before_host_access(monkeypatch):
    import sys
    from types import SimpleNamespace
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(sys, "flags", SimpleNamespace(isolated=True, no_site=True))
    env = {
        "ARENA_RUNTIME_DEPLOYMENT": "production",
        "ARENA_RUNTIME_PRODUCTION_ACK": "I_UNDERSTAND_RUNTIME_EXECUTION",
        "ARENA_RUNTIME_BACKEND": "systemd",
        "ARENA_RUNTIME_EXECUTION_ENABLED": "1",
        "ARENA_RUNTIME_SUPERVISOR_BUNDLE": "/tmp/untrusted-supervisor.pyz",
        "ARENA_RUNTIME_SUPERVISOR_BUNDLE_SHA256": "a" * 64,
    }
    with pytest.raises(SupervisorServiceConfigError, match="Non-canonical supervisor bundle"):
        build_service(env)


def test_recovery_test_profile_requires_dedicated_ack_before_paths(monkeypatch):
    import sys
    from types import SimpleNamespace
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(sys, "flags", SimpleNamespace(isolated=True, no_site=True))
    env = {
        "ARENA_RUNTIME_DEPLOYMENT": "test-recovery",
        "ARENA_RUNTIME_BACKEND": "systemd",
        "ARENA_RUNTIME_EXECUTION_ENABLED": "0",
        "ARENA_RUNTIME_TEST_ROOT": "/var/tmp/arena-test-root",
    }
    with pytest.raises(SupervisorServiceConfigError, match="test acknowledgement"):
        build_service(env)


def test_recovery_test_profile_rejects_execution_opt_in(monkeypatch):
    import sys
    from types import SimpleNamespace
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(sys, "flags", SimpleNamespace(isolated=True, no_site=True))
    env = {
        "ARENA_RUNTIME_DEPLOYMENT": "test-recovery",
        "ARENA_RUNTIME_TEST_ACK": "I_UNDERSTAND_RECOVERY_ONLY_TEST",
        "ARENA_RUNTIME_BACKEND": "systemd",
        "ARENA_RUNTIME_EXECUTION_ENABLED": "1",
    }
    with pytest.raises(SupervisorServiceConfigError, match="execution disabled"):
        build_service(env)
