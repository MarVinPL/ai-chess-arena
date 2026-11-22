"""Coordinator assignment -> worker pull -> result -> checkpoint CAS, offline."""
import json
import time
from threading import Thread
from urllib import request, error

import chess
import pytest
import tournament_checkpoint
from remote_worker_http_ingest import create_loopback_server
from remote_worker_tokens import WorkerTokenStore
from test_remote_worker_job_binding import setup_game


def test_coordinator_worker_roundtrip_and_atomic_checkpoint(tmp_path):
    path, jobs, _, digest = setup_game(tmp_path)
    worker_id, job_id = 'worker_12345678', 'job_12345678'
    token = WorkerTokenStore(path).issue(worker_id, ttl_seconds=600)
    jobs.assign_bound(worker_id, job_id, tournament_id=1, pairing_id=7,
                      expected_sha256=digest, timeout_seconds=90)
    with jobs._db() as db:
        db.execute('UPDATE worker_job_safety SET deadline=? WHERE worker_id=?',
                   (time.time() + 90, worker_id))
    server = create_loopback_server(path)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    def post(endpoint, body):
        req = request.Request(f'http://127.0.0.1:{server.server_port}/internal/remote-worker/{endpoint}',
                              json.dumps(body).encode(),
                              {'Content-Type': 'application/json'}, method='POST')
        try:
            with request.urlopen(req, timeout=3) as response:
                return response.status, json.load(response)
        except error.HTTPError as exc:
            return exc.code, None
    try:
        credentials = {'worker_id': worker_id, 'token': token}
        status, data = post('job', credentials)
        assert status == 200
        assert data['job']['job_id'] == job_id
        assert data['job']['fen'] == tournament_checkpoint.load(path, 1)['fen']
        board = chess.Board(data['job']['fen'])
        move = chess.Move.from_uci('e2e4')
        assert move in board.legal_moves
        assert post('result', {**credentials, 'result': {'version': 1,
                    'job_id': job_id, 'uci': move.uci()}})[0] == 202
        assert post('job', credentials) == (200, {'job': None})
        assert post('result', {**credentials, 'result': {'version': 1,
                    'job_id': job_id, 'uci': move.uci()}})[0] == 401
        assert len(jobs.pending()) == 1
        checkpoint = tournament_checkpoint.load(path, 1)
        board.push(move)
        record = {'uci': move.uci(), 'ply': 1, 'color': 'white',
                  'fen': board.fen(), 'seconds': 0.1}
        updated = {**checkpoint, 'moves': checkpoint['moves'] + [record],
                   'move_times': checkpoint['move_times'] + [0.1],
                   'fen': board.fen(), 'side_to_move': 'black'}
        assert jobs.apply_bound(job_id, updated) is True
        assert jobs.pending() == []
        assert tournament_checkpoint.load(path, 1)['moves'][-1] == record
        assert jobs.apply_bound(job_id, updated) is False
        assert len(tournament_checkpoint.load(path, 1)['moves']) == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
