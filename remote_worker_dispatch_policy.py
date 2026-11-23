"""Fail-closed policy for choosing local versus remote tournament execution.

Remote mode must never silently fall back to local after reserving a job.
The production server is not wired to this policy yet.
"""


class RemoteDispatchUnavailable(RuntimeError):
    pass


def choose_execution(mode, *, remote_enabled=False, coordinator_lock_held=False,
                     worker_ready=False, checkpoint_owner='local'):
    """Return local/remote only when the selected checkpoint writer is unique.

    The caller must hold the coordinator lock for the entire remote game,
    not merely at selection time. No implicit fallback after remote failure.
    """
    if mode not in ('local', 'remote'):
        raise ValueError('Unknown execution mode')
    if mode == 'local':
        if checkpoint_owner != 'local':
            raise RemoteDispatchUnavailable('Local runner does not own checkpoint')
        return 'local'
    if remote_enabled is not True:
        raise RemoteDispatchUnavailable('Remote dispatch disabled')
    if coordinator_lock_held is not True:
        raise RemoteDispatchUnavailable('Coordinator ownership not established')
    if worker_ready is not True:
        raise RemoteDispatchUnavailable('Remote worker not ready')
    if checkpoint_owner != 'remote':
        raise RemoteDispatchUnavailable('Remote runner does not own checkpoint')
    return 'remote'
