"""Offline contract test for the live server checkpoint payload layout."""
import sqlite3
import chess
import tournament_checkpoint
from remote_worker_runner_bridge import RemoteRunnerBridge
from engine_runner import SequentialChessRunner


class NoLocalInference:
    def generate(self, *args, **kwargs):
        raise AssertionError('Local inference must not run')


def test_remote_bridge_preserves_live_checkpoint_metadata(tmp_path):
    path = tmp_path / 'arena.sqlite'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE tournaments (id INTEGER PRIMARY KEY)')
        db.execute('INSERT INTO tournaments VALUES (1)')
    tournament_checkpoint.ensure_schema(path)
    board = chess.Board()
    initial = {
        'schema_version': 1, 'tournament_id': 1, 'phase': 'game',
        'tournament': {'id': 1, 'format': 'league'},
        'entries': [{'model_id': 11}, {'model_id': 22}],
        'schedule': [{'id': 7, 'status': 'running'}],
        'pairing': {'id': 7, 'stage': 'league', 'white_model_id': 11,
                    'black_model_id': 22},
        'white': {'model_id': 11, 'name': 'White'},
        'black': {'model_id': 22, 'name': 'Black'},
        'game_started': '2026-11-22T00:00:00Z',
        'moves': [], 'move_times': [], 'fen': board.fen(),
        'side_to_move': 'white', 'updated_at': '2026-11-22T00:00:00Z',
    }
    tournament_checkpoint.save(path, 1, initial)
    bridge = RemoteRunnerBridge(path, 1, 7, 'worker_12345678',
                                lambda job, board, player: {
                                    'version': 1, 'job_id': job['job_id'], 'uci': 'e2e4'},
                                clock=lambda: 100)
    class Stop:
        stopped = False
        def is_set(self):
            return self.stopped
    stop = Stop()
    def publish(record):
        bridge.publish(record)
        stop.stopped = True
    result = SequentialChessRunner(NoLocalInference(), move_provider=bridge.provide).play_game(
        {'name': 'White', 'model_id': 11}, {'name': 'Black', 'model_id': 22},
        stop_event=stop, progress=publish)
    assert result['status'] == 'stopped'
    final = tournament_checkpoint.load(path, 1)
    assert len(final['moves']) == 1
    assert final['moves'][0]['uci'] == 'e2e4'
    assert final['move_times'] == [0.1]
    assert all(final[key] == initial[key] for key in initial
               if key not in {'moves', 'move_times', 'fen', 'side_to_move', 'updated_at'})
    assert bridge.journal.pending() == []
