"""Offline runner-to-HTTP-worker integration; no production dispatch."""
import json
import threading
import time
from urllib import request

import chess
import tournament_checkpoint
from engine_runner import SequentialChessRunner
from remote_worker_coordinator_transport import StagedResultTransport
from remote_worker_http_ingest import create_loopback_server
from remote_worker_runner_bridge import RemoteRunnerBridge
from remote_worker_tokens import WorkerTokenStore
from remote_worker_heartbeat import WorkerHeartbeatStore
from remote_worker_job_binding import BoundWorkerResults
from test_remote_worker_job_binding import setup_game


class NoLocalInference:
    def generate(self, *args, **kwargs):
        raise AssertionError('Local inference must not run')


def test_runner_dispatches_bound_job_via_http_pull_and_commits_once(tmp_path):
    path, _, _, _ = setup_game(tmp_path)
    worker_id = 'worker_12345678'
    token = WorkerTokenStore(path).issue(worker_id, ttl_seconds=600)
    with BoundWorkerResults(path)._db() as db:
        db.execute("INSERT INTO worker_job_safety VALUES (?,NULL,'idle_confirmed',NULL,0)", (worker_id,))
    server = create_loopback_server(path)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    def post(endpoint, payload):
        req = request.Request(f'http://127.0.0.1:{server.server_port}/internal/remote-worker/{endpoint}',
                              json.dumps(payload).encode(),
                              {'Content-Type': 'application/json'}, method='POST')
        with request.urlopen(req, timeout=3) as response:
            return response.status, json.load(response)
    credentials = {'worker_id': worker_id, 'token': token}
    transport = StagedResultTransport(path, worker_id, poll_interval=0.01)
    worker_errors = []
    def infer(job, board, player):
        def worker():
            try:
                for _ in range(100):
                    status, data = post('job', credentials)
                    if data['job'] is not None:
                        break
                    time.sleep(0.01)
                else:
                    raise AssertionError('No assigned job')
                assert data['job']['job_id'] == job['job_id']
                assert data['job']['fen'] == board.fen()
                assert post('result', {**credentials, 'result': {
                    'version': 1, 'job_id': job['job_id'], 'uci': 'e2e4'}})[0] == 202
            except Exception as exc:
                worker_errors.append(exc)
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        try:
            return transport.wait(job, board, player, timeout=3)
        finally:
            thread.join(timeout=3)
    assert post('heartbeat', credentials)[0] == 202
    assert WorkerHeartbeatStore(path).recent(worker_id)
    bridge = RemoteRunnerBridge(path, 1, 7, worker_id, infer, require_fresh_heartbeat=True)
    class StopAfterMove:
        stopped = False
        def is_set(self):
            return self.stopped
    stop = StopAfterMove()
    def publish(record):
        bridge.publish(record)
        stop.stopped = True
    try:
        result = SequentialChessRunner(NoLocalInference(), move_provider=bridge.provide).play_game(
            {'name': 'White'}, {'name': 'Black'}, stop_event=stop, progress=publish, variant="standard")
        assert not worker_errors, worker_errors
        assert result['status'] == 'stopped'
        assert [m['uci'] for m in tournament_checkpoint.load(path, 1)['moves']] == ['e2e4']
        assert bridge.journal.pending() == []
        assert post('job', credentials) == (200, {'job': None})
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=3)
