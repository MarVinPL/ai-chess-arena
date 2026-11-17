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
