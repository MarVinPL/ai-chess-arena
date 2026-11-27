"""Remote mode must hold exclusive ownership until the game exits."""
import pytest
from remote_worker_execution_session import RemoteGameSession
from remote_worker_coordinator_lock import CoordinatorLock, CoordinatorAlreadyRunning
from remote_worker_dispatch_policy import RemoteDispatchUnavailable


def test_remote_session_owns_lock_for_full_game(tmp_path):
    db = tmp_path / 'arena.sqlite'
    constructed = []
    session = RemoteGameSession(db, remote_factory=lambda: constructed.append('remote') or object(),
                                worker_ready=True, remote_enabled=True)
    with session as runner:
        assert runner is session.runner
        assert constructed == ['remote']
        with pytest.raises(CoordinatorAlreadyRunning):
            with CoordinatorLock(db):
                pass
    assert session.runner is None
    with CoordinatorLock(db):
        pass


@pytest.mark.parametrize('enabled,ready', [(False, True), (True, False), (False, False)])
def test_disabled_or_unready_remote_fails_without_leaking_lock(tmp_path, enabled, ready):
    db = tmp_path / 'arena.sqlite'
    constructed = []
    session = RemoteGameSession(db, remote_factory=lambda: constructed.append(True),
                                worker_ready=ready, remote_enabled=enabled)
    with pytest.raises(RemoteDispatchUnavailable):
        with session:
            pass
    assert constructed == []
    with CoordinatorLock(db):
        pass


def test_remote_factory_failure_releases_lock(tmp_path):
    db = tmp_path / 'arena.sqlite'
    def fail():
        raise RuntimeError('Worker startup failed')
    with pytest.raises(RuntimeError, match='Worker startup failed'):
        with RemoteGameSession(db, remote_factory=fail, worker_ready=True,
                               remote_enabled=True):
            pass
    with CoordinatorLock(db):
        pass


def test_remote_game_exception_releases_lock_without_local_fallback(tmp_path):
    db = tmp_path / 'arena.sqlite'
    with pytest.raises(RuntimeError, match='Game interrupted'):
        with RemoteGameSession(db, remote_factory=lambda: object(),
                               worker_ready=True, remote_enabled=True):
            raise RuntimeError('Game interrupted')
    with CoordinatorLock(db):
        pass
