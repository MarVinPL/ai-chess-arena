"""Fail-closed startup ordering for runtime recovery."""
from __future__ import annotations


class RuntimeStartupRecoveryError(RuntimeError):
    """Raised when startup cannot prove runtime recovery completed safely."""


def recover_runtime_on_startup(*, deployment, supervisor_recover, executor_recover):
    """Recover systemd leases before reconciling API jobs.

    Production must provide a supervisor recovery operation. Never mark API
    jobs failed while an exact systemd unit may still be alive.
    """
    if not callable(executor_recover):
        raise TypeError("Executor recovery callback is required.")

    supervisor_result = None
    if getattr(deployment, "production_requested", False):
        if not callable(supervisor_recover):
            raise RuntimeStartupRecoveryError(
                "Production startup blocked: supervisor lease recovery is not wired."
            )
        try:
            supervisor_result = supervisor_recover()
        except Exception as exc:
            raise RuntimeStartupRecoveryError(
                "Production startup blocked: supervisor recovery failed."
            ) from exc
        if (not isinstance(supervisor_result, dict)
                or type(supervisor_result.get("count")) is not int
                or supervisor_result["count"] < 0):
            raise RuntimeStartupRecoveryError(
                "Production startup blocked: supervisor recovery returned invalid evidence."
            )

    try:
        executor_result = executor_recover()
    except Exception as exc:
        raise RuntimeStartupRecoveryError(
            "Startup blocked: executor job reconciliation failed."
        ) from exc
    if not isinstance(executor_result, dict) or type(executor_result.get("count")) is not int:
        raise RuntimeStartupRecoveryError(
            "Startup blocked: executor recovery returned invalid evidence."
        )
    return {"supervisor": supervisor_result, "executor": executor_result}
