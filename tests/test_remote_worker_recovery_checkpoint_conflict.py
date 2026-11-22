"""Restart replay must reject changed checkpoint without consuming staged result."""
import chess
import pytest
import tournament_checkpoint
from remote_worker_job_binding import BoundWorkerResults
from remote_worker_runner_bridge import RemoteRunnerBridge
from remote_worker_recovery_report import inspect_unresolved_jobs
from test_remote_worker_job_binding import setup_game


def test_staged_move_replay_refuses_conflicting_checkpoint(tmp_path):
    path, _, original, digest = setup_game(tmp_path)
    worker, job = 'worker_12345678', 'job_12345678'
    jobs = BoundWorkerResults(path)
    jobs.assign_bound(worker, job, tournament_id=1, pairing_id=7,
                      expected_sha256=digest, timeout_seconds=90)
    jobs.stage_validated(worker, job, {'version': 1, 'job_id': job, 'uci': 'e2e4'})
    jobs.recover_after_restart()
    board = chess.Board()
    board.push_uci('e2e4')
    record = {'uci': 'e2e4', 'ply': 1, 'color': 'white',
              'fen': board.fen(), 'seconds': 0.1}
    changed = {**original, 'updated_at': 1234}
    tournament_checkpoint.save(path, 1, changed)
    bridge = RemoteRunnerBridge(path, 1, 7, worker, lambda *args: None)
    with pytest.raises(ValueError, match='Source checkpoint changed'):
        bridge.replay_staged(job, record)
    assert tournament_checkpoint.load(path, 1) == changed
    assert len(jobs.pending()) == 1
    assert jobs.state(worker) == 'unknown'
    report = inspect_unresolved_jobs(path)
    assert report[0]['action'] == 'manual_checkpoint_conflict'
    assert report[0]['checkpoint_matches'] is False
