import sqlite3
import chess
import tournament_checkpoint
from engine_runner import SequentialChessRunner
from remote_worker_runner_bridge import RemoteRunnerBridge


class NoLocalRuntime:
    def generate(self, *args, **kwargs):
        raise AssertionError('Unexpected local inference')


def test_complete_chess_game_with_durable_remote_bridge_and_restart(tmp_path):
    path = tmp_path / 'arena.sqlite'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE tournaments (id INTEGER PRIMARY KEY)')
        db.execute('INSERT INTO tournaments VALUES (1)')
    tournament_checkpoint.ensure_schema(path)
    original = {'schema_version': 1, 'tournament_id': 1, 'phase': 'game',
                'pairing': {'id': 7}, 'moves': [], 'move_times': [],
                'fen': chess.Board().fen(), 'side_to_move': 'white'}
    tournament_checkpoint.save(path, 1, original)
    moves = iter(['f2f3', 'e7e5', 'g2g4', 'd8h4'])

    def infer(job, board, player):
        return {'version': 1, 'job_id': job['job_id'], 'uci': next(moves)}

    bridge = RemoteRunnerBridge(path, 1, 7, 'worker_12345678', infer, clock=lambda: 100)
    runner = SequentialChessRunner(NoLocalRuntime(), move_provider=bridge.provide)
    # Simulate restart after two committed moves; a new bridge reads the durable checkpoint.
    class StopAfterTwo:
        def __init__(self):
            self.stopped = False
        def is_set(self):
            return self.stopped
    stop = StopAfterTwo()

    def progress(record):
        bridge.publish(record)
        if record['ply'] == 2:
            stop.stopped = True

    partial = runner.play_game({'name': 'White'}, {'name': 'Black'}, stop_event=stop, progress=progress)
    assert partial['status'] == 'stopped'
    saved = tournament_checkpoint.load(path, 1)
    assert len(saved['moves']) == 2
    bridge.journal.recover_after_restart()
    resumed_bridge = RemoteRunnerBridge(path, 1, 7, 'worker_12345678', infer, clock=lambda: 110)
    # Test harness supplies independent confirmations after simulated restart.
    resumed_bridge.journal.confirm_idle_after_restart('worker_12345678',
        operator_confirmed=True, worker_reconnected=True)
    resumed_runner = SequentialChessRunner(NoLocalRuntime(), move_provider=resumed_bridge.provide)
    result = resumed_runner.play_game({'name': 'White'}, {'name': 'Black'},
        resume_moves=saved['moves'], resume_fen=saved['fen'],
        resume_move_times=saved['move_times'], progress=resumed_bridge.publish)
    assert result['result'] == '0-1'
    final = tournament_checkpoint.load(path, 1)
    assert [m['uci'] for m in final['moves']] == ['f2f3', 'e7e5', 'g2g4', 'd8h4']
    assert len(resumed_bridge.journal.pending()) == 0


def test_transport_failure_keeps_worker_reserved_and_bridge_blocked(tmp_path):
    import pytest
    from remote_worker_jobs import DurableWorkerJobs
    path = tmp_path / 'arena.sqlite'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE tournaments (id INTEGER PRIMARY KEY)')
        db.execute('INSERT INTO tournaments VALUES (1)')
    tournament_checkpoint.ensure_schema(path)
    initial = {'schema_version': 1, 'tournament_id': 1, 'phase': 'game',
               'pairing': {'id': 7}, 'moves': [], 'move_times': [],
               'fen': chess.Board().fen(), 'side_to_move': 'white'}
    tournament_checkpoint.save(path, 1, initial)

    def broken(job, board, player):
        raise ConnectionError('worker disconnected')

    bridge = RemoteRunnerBridge(path, 1, 7, 'worker_12345678', broken, clock=lambda: 100)
    with pytest.raises(ConnectionError, match='disconnected'):
        bridge.provide(chess.Board(), {'name': 'White'}, None, {})
    assert bridge.inflight is not None
    assert DurableWorkerJobs(path, clock=lambda: 100).state('worker_12345678') == 'running'
    with pytest.raises(RuntimeError, match='Previous move not committed'):
        bridge.provide(chess.Board(), {'name': 'White'}, None, {})
    assert tournament_checkpoint.load(path, 1) == initial
    bridge.journal.recover_after_restart()
    assert DurableWorkerJobs(path, clock=lambda: 110).state('worker_12345678') == 'unknown'


def test_transport_failure_does_not_release_worker_or_advance_checkpoint(tmp_path):
    import pytest
    path = tmp_path / 'arena.sqlite'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE tournaments (id INTEGER PRIMARY KEY)')
        db.execute('INSERT INTO tournaments VALUES (1)')
    tournament_checkpoint.ensure_schema(path)
    original = {'schema_version': 1, 'tournament_id': 1, 'phase': 'game',
                'pairing': {'id': 7}, 'moves': [], 'move_times': [],
                'fen': chess.Board().fen(), 'side_to_move': 'white'}
    tournament_checkpoint.save(path, 1, original)

    def broken_infer(job, board, player):
        raise ConnectionError('Worker disconnected')

    bridge = RemoteRunnerBridge(path, 1, 7, 'worker_12345678', broken_infer, clock=lambda: 100)
    with pytest.raises(ConnectionError, match='disconnected'):
        bridge.provide(chess.Board(), {'name': 'White'}, None, {})
    assert bridge.inflight is not None
    assert bridge.journal.state('worker_12345678') == 'running'
    assert tournament_checkpoint.load(path, 1) == original


def test_restart_replays_staged_result_without_worker_idle_claim(tmp_path):
    path = tmp_path / 'arena.sqlite'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE tournaments (id INTEGER PRIMARY KEY)')
        db.execute('INSERT INTO tournaments VALUES (1)')
    tournament_checkpoint.ensure_schema(path)
    board = chess.Board()
    original = {'schema_version': 1, 'tournament_id': 1, 'phase': 'game',
                'pairing': {'id': 7}, 'moves': [], 'move_times': [],
                'fen': board.fen(), 'side_to_move': 'white'}
    digest = tournament_checkpoint.save(path, 1, original)['sha256']
    bridge = RemoteRunnerBridge(path, 1, 7, 'worker_12345678', lambda *args: None, clock=lambda: 100)
    job = 'job_12345678'
    bridge.journal.assign_bound(bridge.worker_id, job, tournament_id=1,
                                pairing_id=7, expected_sha256=digest, timeout_seconds=90)
    bridge.journal.stage_validated(bridge.worker_id, job, {'version': 1, 'job_id': job, 'uci': 'e2e4'})
    bridge.journal.recover_after_restart()
    resumed = RemoteRunnerBridge(path, 1, 7, bridge.worker_id, lambda *args: None, clock=lambda: 110)
    board.push_uci('e2e4')
    record = {'uci': 'e2e4', 'ply': 1, 'color': 'white', 'fen': board.fen(), 'seconds': 0.1}
    resumed.replay_staged(job, record)
    assert len(tournament_checkpoint.load(path, 1)['moves']) == 1
    assert resumed.journal.state(bridge.worker_id) == 'unknown'
    assert resumed.journal.pending() == []
    import pytest
    with pytest.raises(ValueError, match='No staged result'):
        resumed.replay_staged(job, record)
