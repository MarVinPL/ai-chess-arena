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
    old = {'schema_version': 1, 'tournament_id': 1, 'phase': 'game', 'pairing': {'id': 7}, 'moves': [], 'move_times': [], 'fen': board.fen(), 'side_to_move': 'white'}
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
    new = {**old, 'moves': [{'uci': 'e2e4', 'ply': 1, 'color': 'white', 'fen': board.fen(), 'seconds': 0.1}], 'move_times': [0.1], 'fen': board.fen(), 'side_to_move': 'black'}
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


def test_same_position_cannot_be_assigned_twice_while_running(tmp_path):
    path, journal, old, digest = setup_game(tmp_path)
    journal.assign_bound('worker_12345678', 'job_12345678', tournament_id=1,
                         pairing_id=7, expected_sha256=digest, timeout_seconds=90)
    with pytest.raises(ValueError, match='outstanding remote job'):
        journal.assign_bound('worker_87654321', 'job_87654321', tournament_id=1,
                             pairing_id=7, expected_sha256=digest, timeout_seconds=90)
    assert journal.state('worker_87654321') == 'unknown'


def test_same_position_cannot_be_assigned_while_result_waits_for_application(tmp_path):
    path, journal, old, digest = setup_game(tmp_path)
    journal.assign_bound('worker_12345678', 'job_12345678', tournament_id=1,
                         pairing_id=7, expected_sha256=digest, timeout_seconds=90)
    journal.stage_validated('worker_12345678', 'job_12345678',
                            {'version': 1, 'job_id': 'job_12345678', 'uci': 'e2e4'})
    with pytest.raises(ValueError, match='outstanding remote job'):
        journal.assign_bound('worker_87654321', 'job_87654321', tournament_id=1,
                             pairing_id=7, expected_sha256=digest, timeout_seconds=90)
    assert len(journal.pending()) == 1


def test_bound_result_cannot_rewrite_tournament_metadata(tmp_path):
    path, journal, old, digest = setup_game(tmp_path)
    worker, job = 'worker_12345678', 'job_12345678'
    journal.assign_bound(worker, job, tournament_id=1, pairing_id=7,
                         expected_sha256=digest, timeout_seconds=90)
    journal.stage_validated(worker, job, {'version': 1, 'job_id': job, 'uci': 'e2e4'})
    board = chess.Board(old['fen'])
    board.push_uci('e2e4')
    valid = {**old, 'moves': [{'uci': 'e2e4', 'ply': 1, 'color': 'white',
              'fen': board.fen(), 'seconds': 0.1}], 'move_times': [0.1],
              'fen': board.fen(), 'side_to_move': 'black'}
    with pytest.raises(ValueError, match='Immutable checkpoint metadata'):
        journal.apply_bound(job, {**valid, 'schedule': [{'winner_model_id': 999}]})
    assert tournament_checkpoint.load(path, 1) == old
    assert len(journal.pending()) == 1
    assert journal.apply_bound(job, valid)


def test_restart_reconciliation_fails_closed_without_independent_confirmation(tmp_path):
    path, journal, old, digest = setup_game(tmp_path)
    worker, job = 'worker_12345678', 'job_12345678'
    journal.assign_bound(worker, job, tournament_id=1, pairing_id=7,
                         expected_sha256=digest, timeout_seconds=90)
    journal.recover_after_restart()
    with pytest.raises(ValueError, match='Independent'):
        journal.confirm_idle_after_restart(worker, operator_confirmed=False, worker_reconnected=True)
    with pytest.raises(ValueError, match='Independent'):
        journal.confirm_idle_after_restart(worker, operator_confirmed=True, worker_reconnected=False)
    assert journal.state(worker) == 'unknown'
    assert journal.confirm_idle_after_restart(worker, operator_confirmed=True, worker_reconnected=True)
    assert journal.state(worker) == 'idle_confirmed'
    with pytest.raises(ValueError, match='not eligible'):
        journal.confirm_idle_after_restart(worker, operator_confirmed=True, worker_reconnected=True)


def test_restart_reconciliation_blocks_unapplied_result(tmp_path):
    path, journal, old, digest = setup_game(tmp_path)
    worker, job = 'worker_12345678', 'job_12345678'
    journal.assign_bound(worker, job, tournament_id=1, pairing_id=7,
                         expected_sha256=digest, timeout_seconds=90)
    journal.stage_validated(worker, job, {'version': 1, 'job_id': job, 'uci': 'e2e4'})
    journal.recover_after_restart()
    with pytest.raises(ValueError, match='unapplied'):
        journal.confirm_idle_after_restart(worker, operator_confirmed=True, worker_reconnected=True)
    assert journal.state(worker) == 'unknown'


def test_worker_cannot_receive_next_job_while_result_unapplied(tmp_path):
    path, journal, old, digest = setup_game(tmp_path)
    worker = 'worker_12345678'
    job = 'job_12345678'
    journal.assign_bound(worker, job, tournament_id=1, pairing_id=7,
                         expected_sha256=digest, timeout_seconds=90)
    journal.stage_validated(worker, job, {'version': 1, 'job_id': job, 'uci': 'e2e4'})
    assert journal.state(worker) == 'idle_confirmed'
    with pytest.raises(ValueError, match='unapplied result'):
        journal.assign_bound(worker, 'job_87654321', tournament_id=1,
                             pairing_id=7, expected_sha256=digest, timeout_seconds=90)
    with journal._db() as db:
        assert db.execute('SELECT count(*) FROM remote_job_binding').fetchone()[0] == 1


def test_restart_unknown_worker_blocks_competing_worker_on_same_position(tmp_path):
    path, journal, old, digest = setup_game(tmp_path)
    journal.assign_bound('worker_12345678', 'job_12345678', tournament_id=1,
                         pairing_id=7, expected_sha256=digest, timeout_seconds=90)
    journal.recover_after_restart()
    assert journal.state('worker_12345678') == 'unknown'
    with pytest.raises(ValueError, match='outstanding remote job'):
        journal.assign_bound('worker_87654321', 'job_87654321', tournament_id=1,
                             pairing_id=7, expected_sha256=digest, timeout_seconds=90)
    assert journal.state('worker_87654321') == 'unknown'
