"""Explicit fail-closed runner selection; production server integration pending.

The caller retains coordinator_lock for the full game and routes checkpoint
writes through the selected runner only. No implicit local fallback.
"""
from remote_worker_dispatch_policy import choose_execution


def select_game_runner(mode, *, local_factory, remote_factory=None,
                       remote_enabled=False, coordinator_lock_held=False,
                       worker_ready=False, checkpoint_owner='local'):
    """Select exactly one runner, without constructing the unselected runner."""
    selected = choose_execution(mode, remote_enabled=remote_enabled,
                                coordinator_lock_held=coordinator_lock_held,
                                worker_ready=worker_ready,
                                checkpoint_owner=checkpoint_owner)
    if selected == 'local':
        return local_factory()
    if remote_factory is None:
        raise ValueError('Remote runner factory not configured')
    return remote_factory()
