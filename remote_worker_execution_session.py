"""Scoped, fail-closed coordinator ownership for an opt-in remote game.

The session owns a POSIX lock for its full lifetime. It does not activate
remote dispatch in the production server or choose a worker automatically.
"""
from remote_worker_coordinator_lock import CoordinatorLock
from remote_worker_runner_selection import select_game_runner


class RemoteGameSession:
    def __init__(self, database, *, remote_factory, worker_ready,
                 remote_enabled=False):
        self.database = database
        self.remote_factory = remote_factory
        self.worker_ready = worker_ready
        self.remote_enabled = remote_enabled
        self._lock = None
        self.runner = None

    def __enter__(self):
        if self._lock is not None:
            raise RuntimeError('Remote session already active')
        lock = CoordinatorLock(self.database)
        lock.acquire()
        try:
            runner = select_game_runner(
                'remote', local_factory=lambda: (_ for _ in ()).throw(
                    AssertionError('Local fallback forbidden')),
                remote_factory=self.remote_factory,
                remote_enabled=self.remote_enabled,
                coordinator_lock_held=True,
                worker_ready=self.worker_ready,
                checkpoint_owner='remote',
            )
        except BaseException:
            lock.close()
            raise
        self._lock = lock
        self.runner = runner
        return runner

    def __exit__(self, *_):
        lock, self._lock = self._lock, None
        self.runner = None
        if lock is not None:
            lock.close()
