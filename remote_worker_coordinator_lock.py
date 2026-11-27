"""POSIX advisory single-coordinator ownership, held for process lifetime.

All coordinator instances sharing a SQLite database must share this lock path.
This is local-filesystem protection, not distributed leader election.
"""
import fcntl
import os
from pathlib import Path


class CoordinatorAlreadyRunning(RuntimeError):
    pass


class CoordinatorLock:
    def __init__(self, database):
        self.path = str(Path(str(database) + '.coordinator.lock'))
        self._fd = None

    def acquire(self):
        if self._fd is not None:
            raise RuntimeError('Coordinator lock already held by this instance')
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(fd)
            raise CoordinatorAlreadyRunning('Another coordinator owns this database') from exc
        except BaseException:
            os.close(fd)
            raise
        self._fd = fd
        return self

    def close(self):
        if self._fd is not None:
            fd, self._fd = self._fd, None
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *_):
        self.close()
