import threading
import time

import chess
import pytest
import tournament_checkpoint
from remote_worker_coordinator_transport import StagedResultTransport, RemoteResultTimeout
from remote_worker_runner_bridge import RemoteRunnerBridge
from remote_worker_authenticated_ingest import AuthenticatedResultIngest
from remote_worker_tokens import WorkerTokenStore
from test_remote_worker_job_binding import setup_game


def test_coordinator_bridge_waits_for_authenticated_result(tmp_path):
    path, _, _, _ = setup_game(tmp_path)
    worker_id = 'worker_12345678'
    token = WorkerTokenStore(path).issue(worker_id, ttl_seconds=600)
    transport = StagedResultTransport(path, worker_id, poll_interval=0.01)
    done = threading.Event()
    def infer(job, board, player):
        def worker():
            try:
                AuthenticatedResultIngest(path).accept(worker_id, token, {
                    'version': 1, 'job_id': job['job_id'], 'uci': 'e2e4'})
            finally:
                done.set()
        threading.Thread(target=worker, daemon=True).start()
        return transport.wait(job, board, player, timeout=3)
    bridge = RemoteRunnerBridge(path, 1, 7, worker_id, infer)
    move, meta = bridge.provide(chess.Board(), {'name': 'White'}, None, {})
    assert done.wait(3)
    assert move.uci() == 'e2e4'
    board = chess.Board()
    board.push(move)
    bridge.publish({'uci': 'e2e4', 'ply': 1, 'color': 'white',
                    'fen': board.fen(), 'seconds': meta['seconds']})
    assert tournament_checkpoint.load(path, 1)['moves'][0]['uci'] == 'e2e4'
    assert bridge.journal.pending() == []


def test_wait_timeout_does_not_release_job(tmp_path):
    path, jobs, _, digest = setup_game(tmp_path)
    jobs.assign_bound('worker_12345678', 'job_12345678', tournament_id=1,
                      pairing_id=7, expected_sha256=digest, timeout_seconds=90)
    transport = StagedResultTransport(path, 'worker_12345678', poll_interval=0.01)
    with pytest.raises(RemoteResultTimeout):
        transport.wait({'job_id': 'job_12345678'}, chess.Board(), {}, timeout=0.03)
    assert jobs.state('worker_12345678') == 'running'
