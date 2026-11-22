import pytest

from remote_worker_dispatch_policy import RemoteDispatchUnavailable
from remote_worker_runner_selection import select_game_runner


def test_default_selects_only_local():
    constructed = []
    def local():
        constructed.append('local')
        return 'local-runner'
    def remote():
        constructed.append('remote')
        return 'remote-runner'
    assert select_game_runner('local', local_factory=local, remote_factory=remote) == 'local-runner'
    assert constructed == ['local']


@pytest.mark.parametrize('overrides', [
    {},
    {'remote_enabled': True},
    {'remote_enabled': True, 'coordinator_lock_held': True},
    {'remote_enabled': True, 'coordinator_lock_held': True, 'worker_ready': True},
])
def test_remote_rejected_without_all_guards(overrides):
    constructed = []
    with pytest.raises(RemoteDispatchUnavailable):
        select_game_runner('remote', local_factory=lambda: constructed.append('local'),
                           remote_factory=lambda: constructed.append('remote'), **overrides)
    assert constructed == []


def test_remote_constructs_only_remote_when_fully_authorized():
    constructed = []
    opts = {'remote_enabled': True, 'coordinator_lock_held': True,
            'worker_ready': True, 'checkpoint_owner': 'remote'}
    assert select_game_runner('remote', local_factory=lambda: constructed.append('local'),
                              remote_factory=lambda: 'remote-runner', **opts) == 'remote-runner'
    assert constructed == []


def test_missing_remote_factory_does_not_fallback():
    with pytest.raises(ValueError, match='not configured'):
        select_game_runner('remote', local_factory=lambda: 'local',
                           remote_enabled=True, coordinator_lock_held=True,
                           worker_ready=True, checkpoint_owner='remote')
