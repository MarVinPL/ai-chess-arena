import multiprocessing
import pytest
from remote_worker_coordinator_lock import CoordinatorAlreadyRunning, CoordinatorLock


def _try_lock(path, queue):
    try:
        with CoordinatorLock(path):
            queue.put('acquired')
    except CoordinatorAlreadyRunning:
        queue.put('blocked')


def test_exclusive_across_processes_and_release(tmp_path):
    path = tmp_path / 'jobs.sqlite'
    ctx = multiprocessing.get_context('spawn')
    with CoordinatorLock(path):
        queue = ctx.Queue()
        process = ctx.Process(target=_try_lock, args=(path, queue))
        process.start()
        assert queue.get(timeout=10) == 'blocked'
        process.join(timeout=10)
        assert process.exitcode == 0
    queue = ctx.Queue()
    process = ctx.Process(target=_try_lock, args=(path, queue))
    process.start()
    assert queue.get(timeout=10) == 'acquired'
    process.join(timeout=10)
    assert process.exitcode == 0


def test_failure_to_acquire_does_not_release_owner(tmp_path):
    path = tmp_path / 'jobs.sqlite'
    with CoordinatorLock(path):
        with pytest.raises(CoordinatorAlreadyRunning):
            CoordinatorLock(path).acquire()
        with pytest.raises(CoordinatorAlreadyRunning):
            CoordinatorLock(path).acquire()
