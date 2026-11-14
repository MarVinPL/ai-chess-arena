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

    def test_resume_rejects_san_that_does_not_match_uci(self):
        stop = threading.Event()
        runner = engine_runner.SequentialChessRunner(_StopDuringInference(stop))
        player = {'name': 'Model', 'path': '/tmp/model.gguf', 'model_id': 1}
        with self.assertRaisesRegex(ValueError, 'SAN nie zgadza się'):
            runner.play_game(player, player, stop_event=stop,
                             resume_moves=[{'ply': 1, 'uci': 'e2e4', 'san': 'd4',
                                            'color': 'white', 'model': 'Model', 'seconds': 0.1}])

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


class PairingSelectionRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / 'pairing-recovery.sqlite3'
        self.old_server_db = server.DB
        self.old_league_db = server.league.DB
        server.DB = self.db
        server.league.DB = self.db
        server.db_init()
        with server.league.connect() as db:
            db.execute("INSERT INTO tournaments(id,name,format,status,created_at,rules_version) VALUES(77,'Recovery','PURE_AI','stopped','2026-11-14T00:00:00Z','2')")
            for model_id, name in ((1, 'White'), (2, 'Black')):
                db.execute("INSERT INTO league_models(id,name,format,active,created_at) VALUES(?,?, 'PURE_AI',1,'2026-11-14T00:00:00Z')", (model_id, name))
            db.execute("INSERT INTO tournament_pairings(id,tournament_id,white_model_id,black_model_id,leg,status,stage) VALUES(101,77,1,2,1,'running','league')")
    def tearDown(self):
        server.DB = self.old_server_db
        server.league.DB = self.old_league_db
        self.temp.cleanup()
    def save_checkpoint(self, pairing_id=101):
        tournament_checkpoint.save(self.db, 77, {
            'schema_version': tournament_checkpoint.SCHEMA_VERSION,
            'tournament_id': 77, 'phase': 'game',
            'pairing': {'id': pairing_id, 'stage': 'league', 'white_model_id': 1, 'black_model_id': 2},
            'moves': [], 'move_times': [], 'fen': chess.Board().fen(),
        })
    def test_running_pairing_is_selected_from_checkpoint_after_restart(self):
        self.save_checkpoint()
        pairing, checkpoint = server._select_tournament_pairing(77, False)
        self.assertEqual((pairing['id'], pairing['status']), (101, 'running'))
        self.assertEqual(checkpoint['pairing']['id'], 101)
    def test_pending_pairing_is_selected_from_checkpoint(self):
        with server.league.connect() as db:
            db.execute("UPDATE tournament_pairings SET status='pending' WHERE id=101")
        self.save_checkpoint()
        pairing, checkpoint = server._select_tournament_pairing(77, False)
        self.assertEqual((pairing['id'], pairing['status']), (101, 'pending'))
        self.assertIsNotNone(checkpoint)
    def test_running_pairing_without_checkpoint_fails_closed(self):
        with self.assertRaisesRegex(RuntimeError, 'bez checkpointu'):
            server._select_tournament_pairing(77, False)
    def test_multiple_running_pairings_fail_closed(self):
        with server.league.connect() as db:
            db.execute("INSERT INTO tournament_pairings(id,tournament_id,white_model_id,black_model_id,leg,status,stage) VALUES(102,77,2,1,1,'running','league')")
        self.save_checkpoint()
        with self.assertRaisesRegex(RuntimeError, 'więcej niż jedno'):
            server._select_tournament_pairing(77, False)
    def test_checkpoint_must_match_running_pairing(self):
        self.save_checkpoint()
        with server.league.connect() as db:
            db.execute("UPDATE tournament_pairings SET id=102 WHERE id=101")
        with self.assertRaisesRegex(RuntimeError, 'inne parowanie'):
            server._select_tournament_pairing(77, False)



if __name__ == '__main__':
    unittest.main()
