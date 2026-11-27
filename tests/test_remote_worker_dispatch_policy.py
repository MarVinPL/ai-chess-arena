import itertools
import pytest
from remote_worker_dispatch_policy import choose_execution, RemoteDispatchUnavailable


def test_local_is_default_and_cannot_claim_remote_checkpoint():
    assert choose_execution('local') == 'local'
    with pytest.raises(RemoteDispatchUnavailable):
        choose_execution('local', checkpoint_owner='remote')


def test_remote_requires_all_safety_conditions():
    for enabled, lock, ready, owner in itertools.product(
            (False, True), (False, True), (False, True), ('local', 'remote')):
        options = dict(remote_enabled=enabled, coordinator_lock_held=lock,
                       worker_ready=ready, checkpoint_owner=owner)
        if enabled and lock and ready and owner == 'remote':
            assert choose_execution('remote', **options) == 'remote'
        else:
            with pytest.raises(RemoteDispatchUnavailable):
                choose_execution('remote', **options)


def test_invalid_mode_fails_closed():
    with pytest.raises(ValueError):
        choose_execution('auto')
