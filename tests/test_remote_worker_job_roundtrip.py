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

def test_two_sequential_remote_http_moves_commit_in_order_and_only_once(tmp_path):
    """Simulation: two HTTP proposals, coordinator authoritatively commits each.

    This checks the transport/checkpoint seam. A synthetic test client is not
    proof that a real volunteer machine can execute a GGUF inference task.
    """
    from remote_worker_job_binding import BoundWorkerResults

    path, _, _, _ = setup_game(tmp_path)
    worker_id = 'worker_12345678'
    credentials = {
        'worker_id': worker_id,
        'token': WorkerTokenStore(path).issue(worker_id, ttl_seconds=600),
    }
    journal = BoundWorkerResults(path)
    server = create_loopback_server(path)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def post(endpoint, body):
        req = request.Request(
            f'http://127.0.0.1:{server.server_port}/internal/remote-worker/{endpoint}',
            json.dumps(body).encode(),
            {'Content-Type': 'application/json'}, method='POST',
        )
        try:
            with request.urlopen(req, timeout=3) as response:
                return response.status, json.load(response)
        except error.HTTPError as exc:
            return exc.code, None

    try:
        for ply, uci in enumerate(('e2e4', 'e7e5'), start=1):
            previous = tournament_checkpoint.load(path, 1)
            with journal._db() as db:
                checkpoint_hash = db.execute(
                    'SELECT sha256 FROM tournament_checkpoints WHERE tournament_id=1'
                ).fetchone()[0]
            job_id = f'job_1234567{ply}'
            journal.assign_bound(
                worker_id, job_id, tournament_id=1, pairing_id=7,
                expected_sha256=checkpoint_hash, timeout_seconds=90,
            )
            status, delivery = post('job', credentials)
            assert status == 200
            assert delivery['job'] == {'version': 1, 'job_id': job_id, 'fen': previous['fen']}

            # A move legal for the other side is never accepted for this FEN.
            wrong = 'e7e5' if ply == 1 else 'e2e4'
            assert post('result', {**credentials, 'result': {
                'version': 1, 'job_id': job_id, 'uci': wrong,
            }})[0] == 401

            result = {'version': 1, 'job_id': job_id, 'uci': uci}
            assert post('result', {**credentials, 'result': result})[0] == 202
            assert post('result', {**credentials, 'result': result})[0] == 401
            assert post('job', credentials) == (200, {'job': None})

            board = chess.Board(previous['fen'])
            board.push_uci(uci)
            record = {
                'uci': uci, 'ply': ply,
                'color': 'white' if ply % 2 else 'black',
                'fen': board.fen(), 'seconds': 0.1,
            }
            updated = {
                **previous, 'moves': previous['moves'] + [record],
                'move_times': previous['move_times'] + [0.1],
                'fen': board.fen(),
                'side_to_move': 'white' if board.turn else 'black',
            }
            assert journal.apply_bound(job_id, updated) is True
            assert journal.apply_bound(job_id, updated) is False
            assert journal.pending() == []

        final = tournament_checkpoint.load(path, 1)
        assert [m['uci'] for m in final['moves']] == ['e2e4', 'e7e5']
        assert len(final['move_times']) == 2
        expected = chess.Board()
        expected.push_uci('e2e4')
        expected.push_uci('e7e5')
        assert final['fen'] == expected.fen()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
