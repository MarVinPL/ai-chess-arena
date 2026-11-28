import pytest

from runtime_startup import RuntimeStartupRecoveryError, recover_runtime_on_startup


class Deployment:
    production_requested = True


class DisabledDeployment:
    production_requested = False


def test_production_recovers_supervisor_before_executor():
    calls = []
    result = recover_runtime_on_startup(
        deployment=Deployment(),
        supervisor_recover=lambda: calls.append("supervisor") or {"count": 2},
        executor_recover=lambda: calls.append("executor") or {"count": 2},
    )
    assert calls == ["supervisor", "executor"]
    assert result["supervisor"] == {"count": 2}


def test_production_fails_closed_before_executor_if_supervisor_missing():
    calls = []
    with pytest.raises(RuntimeStartupRecoveryError, match="not wired"):
        recover_runtime_on_startup(
            deployment=Deployment(), supervisor_recover=None,
            executor_recover=lambda: calls.append("executor") or {"count": 0},
        )
    assert calls == []


def test_production_fails_closed_if_supervisor_recovery_fails():
    calls = []
    def fail():
        calls.append("supervisor")
        raise OSError("unavailable")
    with pytest.raises(RuntimeStartupRecoveryError, match="supervisor recovery failed"):
        recover_runtime_on_startup(
            deployment=Deployment(), supervisor_recover=fail,
            executor_recover=lambda: calls.append("executor") or {"count": 0},
        )
    assert calls == ["supervisor"]


def test_disabled_profile_preserves_executor_only_recovery():
    calls = []
    result = recover_runtime_on_startup(
        deployment=DisabledDeployment(), supervisor_recover=None,
        executor_recover=lambda: calls.append("executor") or {"count": 0},
    )
    assert calls == ["executor"]
    assert result == {"supervisor": None, "executor": {"count": 0}}


def test_invalid_supervisor_evidence_blocks_executor():
    calls = []
    with pytest.raises(RuntimeStartupRecoveryError, match="invalid evidence"):
        recover_runtime_on_startup(
            deployment=Deployment(), supervisor_recover=lambda: {"count": True},
            executor_recover=lambda: calls.append("executor") or {"count": 0},
        )
    assert calls == []
