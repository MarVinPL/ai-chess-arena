import sqlite3
import chess
import pytest
import tournament_checkpoint
from remote_worker_job_binding import BoundWorkerResults


def setup_game(tmp_path):
    path = tmp_path / 'arena.sqlite'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE tournaments (id INTEGER PRIMARY KEY)')
        db.execute('INSERT INTO tournaments VALUES (1)')
    tournament_checkpoint.ensure_schema(path)
    board = chess.Board()
    old = {'schema_version': 1, 'tournament_id': 1, 'phase': 'game', 'pairing': {'id': 7}, 'moves': [], 'fen': board.fen(), 'side_to_move': 'white'}
    digest = tournament_checkpoint.save(path, 1, old)['sha256']
    journal = BoundWorkerResults(path, clock=lambda: 100)
    return path, journal, old, digest


def test_bound_result_survives_restart_and_applies_once(tmp_path):
    path, journal, old, digest = setup_game(tmp_path)
    worker, job = 'worker_12345678', 'job_12345678'
    assert journal.assign_bound(worker, job, tournament_id=1, pairing_id=7, expected_sha256=digest, timeout_seconds=90) == old['fen']
    result = {'version': 1, 'job_id': job, 'uci': 'e2e4'}
    journal.stage_validated(worker, job, result)
    journal.recover_after_restart()
    restarted = BoundWorkerResults(path, clock=lambda: 110)
    board = chess.Board(old['fen'])
    board.push_uci('e2e4')
    new = {**old, 'moves': [{'uci': 'e2e4'}], 'fen': board.fen(), 'side_to_move': 'black'}
    assert restarted.apply_bound(job, new)
    assert not restarted.apply_bound(job, new)
    assert tournament_checkpoint.load(path, 1) == new


def test_binding_rejects_wrong_pairing_and_changed_board(tmp_path):
    path, journal, old, digest = setup_game(tmp_path)
    worker, job = 'worker_12345678', 'job_12345678'
    journal.assign_bound(worker, job, tournament_id=1, pairing_id=7, expected_sha256=digest, timeout_seconds=90)
    journal.stage_validated(worker, job, {'version': 1, 'job_id': job, 'uci': 'e2e4'})
    with pytest.raises(ValueError, match='Wrong pairing'):
        journal.apply_bound(job, {**old, 'pairing': {'id': 8}})
    changed = {**old, 'moves': [{'uci': 'd2d4'}]}
    tournament_checkpoint.save(path, 1, changed)
    with pytest.raises(ValueError, match='Source checkpoint changed'):
        journal.apply_bound(job, old)
    assert len(journal.pending()) == 1
