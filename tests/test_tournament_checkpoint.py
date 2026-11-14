import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

import chess
import tournament_checkpoint
import engine_runner
import server


class TournamentCheckpointTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / 'checkpoint.sqlite3'
        with sqlite3.connect(self.db) as db:
            db.execute('CREATE TABLE tournaments(id INTEGER PRIMARY KEY)')
            db.execute('INSERT INTO tournaments(id) VALUES(7)')
        tournament_checkpoint.ensure_schema(self.db)
        self.payload = {
            'schema_version': tournament_checkpoint.SCHEMA_VERSION,
            'tournament_id': 7,
            'phase': 'game',
            'pairing': {'id': 12, 'white_model_id': 1, 'black_model_id': 2},
            'moves': [{'ply': 1, 'uci': 'e2e4', 'seconds': 1.2}],
            'move_times': [1.2],
            'fen': None,
        }
        board = chess.Board(); board.push_uci('e2e4')
        self.payload['fen'] = board.fen()

    def tearDown(self):
        self.temp.cleanup()

    def test_atomic_round_trip_and_checksum(self):
        result = tournament_checkpoint.save(self.db, 7, self.payload)
        restored = tournament_checkpoint.load(self.db, 7)
        self.assertEqual(restored, self.payload)
        self.assertEqual(len(result['sha256']), 64)

    def test_corrupt_payload_fails_closed(self):
        tournament_checkpoint.save(self.db, 7, self.payload)
        with sqlite3.connect(self.db) as db:
            db.execute("UPDATE tournament_checkpoints SET payload_json='{}' WHERE tournament_id=7")
        with self.assertRaisesRegex(ValueError, 'uszkodzony'):
            tournament_checkpoint.load(self.db, 7)

    def test_wrong_tournament_id_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'identyfikatora'):
            tournament_checkpoint.save(self.db, 8, self.payload)

    def test_delete_is_idempotent(self):
        tournament_checkpoint.save(self.db, 7, self.payload)
        tournament_checkpoint.delete(self.db, 7)
        tournament_checkpoint.delete(self.db, 7)
        self.assertIsNone(tournament_checkpoint.load(self.db, 7))


class _StopDuringInference:
    def __init__(self, stop_event):
        self.stop_event = stop_event

    def generate(self, model_path, board, stop_event=None, **kwargs):
        move = chess.Move.from_uci('e2e4') if board.turn == chess.WHITE else chess.Move.from_uci('e7e5')
        self.stop_event.set()
        return move, {'seconds': 0.1, 'budget_observations': []}


class RunnerResumeTests(unittest.TestCase):
    def test_inflight_move_is_discarded_when_stop_arrives(self):
        stop = threading.Event()
        runner = engine_runner.SequentialChessRunner(_StopDuringInference(stop))
        player = {'name': 'Model', 'path': '/tmp/model.gguf', 'model_id': 1}
        with self.assertRaises(engine_runner.RunnerError) as caught:
            runner.play_game(player, player, stop_event=stop)
        self.assertEqual(caught.exception.move_records, [])
        self.assertEqual(caught.exception.fen, chess.Board().fen())

    def test_resume_replays_verified_history_and_preserves_position(self):
        stop = threading.Event()
        board = chess.Board(); board.push_uci('e2e4')
        runner = engine_runner.SequentialChessRunner(_StopDuringInference(stop))
        player = {'name': 'Model', 'path': '/tmp/model.gguf', 'model_id': 1}
        with self.assertRaises(engine_runner.RunnerError) as caught:
            runner.play_game(player, player, stop_event=stop,
                             resume_moves=[{'ply': 1, 'uci': 'e2e4', 'color': 'white', 'model': 'Model', 'seconds': 0.1}],
                             resume_fen=board.fen(), resume_move_times=[0.1])
        self.assertEqual(len(caught.exception.move_records), 1)
        self.assertEqual(caught.exception.fen, board.fen())

    def test_resume_rejects_illegal_history(self):
        stop = threading.Event()
        runner = engine_runner.SequentialChessRunner(_StopDuringInference(stop))
        player = {'name': 'Model', 'path': '/tmp/model.gguf', 'model_id': 1}
        with self.assertRaisesRegex(ValueError, 'nielegalną historię'):
            runner.play_game(player, player, stop_event=stop,
                             resume_moves=[{'uci': 'e2e5'}])


class StartupRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / 'recovery.sqlite3'
        self.old_server_db = server.DB
        self.old_league_db = server.league.DB
        server.DB = self.db
        server.league.DB = self.db
        server.db_init()
        with server.league.connect() as db:
            for tournament_id in (31, 32):
                db.execute("INSERT INTO tournaments(id,name,format,status,created_at) VALUES(?,?,?,?,?)",
                           (tournament_id, f'Tournament {tournament_id}', 'PURE_AI', 'running', '2026-11-14T00:00:00Z'))
        tournament_checkpoint.save(self.db, 31, {
            'schema_version': tournament_checkpoint.SCHEMA_VERSION,
            'tournament_id': 31, 'phase': 'game', 'moves': [], 'move_times': [],
        })

    def tearDown(self):
        server.DB = self.old_server_db
        server.league.DB = self.old_league_db
        self.temp.cleanup()

    def test_startup_recovers_only_tournaments_with_checkpoints(self):
        server.db_init()
        with server.league.connect() as db:
            recovered = db.execute('SELECT status,resume_blocked FROM tournaments WHERE id=31').fetchone()
            blocked = db.execute('SELECT status,resume_blocked FROM tournaments WHERE id=32').fetchone()
        self.assertEqual((recovered['status'], recovered['resume_blocked']), ('stopped', 0))
        self.assertEqual((blocked['status'], blocked['resume_blocked']), ('failed', 1))

    def test_start_refuses_legacy_interruption_without_checkpoint(self):
        server.db_init()
        with self.assertRaisesRegex(RuntimeError, 'ręczny przegląd'):
            server.start_tournament(32)


if __name__ == '__main__':
    unittest.main()
