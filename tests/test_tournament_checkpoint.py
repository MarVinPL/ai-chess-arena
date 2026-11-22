import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

import chess
import tournament_checkpoint
import engine_runner
import server
import league


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


class CheckpointAdmissionAppendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / 'admission-checkpoint.sqlite3'
        self.old_league_db = league.DB
        self.old_server_db = server.DB
        league.DB = self.db
        server.DB = self.db
        league.init()
        with league.connect() as db:
            db.execute('CREATE TABLE games(id INTEGER PRIMARY KEY,white_model TEXT,black_model TEXT,result TEXT)')
        for name, digest in (('Alpha','a'*64),('Beta','b'*64)):
            league.register_model(name,source=f'https://example.test/{name}.gguf',license='Apache-2.0',license_verified=True,
                metadata={'filename':name.lower()+'.gguf','sha256':digest,'size_bytes':100})
        self.parent = league.create_round_robin('Parent with checkpoint')
        league.initialize_late_admission_quota(self.parent['id'])
        with league.connect() as db:
            db.execute("UPDATE tournaments SET status='stopped' WHERE id=?",(self.parent['id'],))
        for name, digest in (('Candidate A','c'*64),('Candidate B','d'*64)):
            league.register_model(name,source=f'https://example.test/{name}.gguf',license='Apache-2.0',license_verified=True,
                metadata={'filename':name.lower().replace(' ','-')+'.gguf','sha256':digest,'size_bytes':100})
        self.batch = league.create_late_admission_batch(self.parent['id'],[3,4])
        with league.connect() as db:
            rows=db.execute("SELECT id,white_model_id,black_model_id,leg,stage,round_no,match_no,status,game_id,winner_model_id FROM tournament_pairings WHERE tournament_id=? AND stage='league' ORDER BY id",(self.parent['id'],)).fetchall()
            self.pairing=dict(rows[0])
            self.saved_schedule=[dict(row) for row in rows]
            self.saved_entries=[dict(row) for row in db.execute('SELECT e.model_id,e.version_id,m.name,m.format,m.active FROM tournament_entries e JOIN league_models m ON m.id=e.model_id WHERE e.tournament_id=? ORDER BY e.model_id',(self.parent['id'],)).fetchall()]
            self.saved_tournament=dict(db.execute('SELECT * FROM tournaments WHERE id=?',(self.parent['id'],)).fetchone())
        self.players={}
        for row in self.saved_entries:
            self.players[row['model_id']]={'model_id':row['model_id'],'name':row['name'],'version_id':row['version_id'],
                'model_sha256':None,'draft_sha256':'','base_inference':{},'inference':{}}
        self.checkpoint={'phase':'game','pairing':{'id':self.pairing['id'],'stage':'league',
            'white_model_id':self.pairing['white_model_id'],'black_model_id':self.pairing['black_model_id']},
            'white':self.players[self.pairing['white_model_id']],'black':self.players[self.pairing['black_model_id']],
            'moves':[],'move_times':[],'fen':chess.Board().fen(),'side_to_move':'white',
            'tournament':self.saved_tournament,'entries':self.saved_entries,'schedule':self.saved_schedule}

    def tearDown(self):
        league.DB=self.old_league_db
        server.DB=self.old_server_db
        self.temp.cleanup()

    def test_append_only_qualified_entrants_do_not_invalidate_existing_checkpoint(self):
        child_id=self.batch['qualification_tournament_id']
        with league.connect() as db:
            pairings=db.execute("SELECT id,white_model_id,black_model_id FROM tournament_pairings WHERE tournament_id=? AND stage='league' ORDER BY id",(child_id,)).fetchall()
            for pairing in pairings:
                white=db.execute('SELECT name FROM league_models WHERE id=?',(pairing['white_model_id'],)).fetchone()['name']
                black=db.execute('SELECT name FROM league_models WHERE id=?',(pairing['black_model_id'],)).fetchone()['name']
                game=db.execute('INSERT INTO games(white_model,black_model,result) VALUES(?,?,?)',(white,black,'1-0'))
                db.execute("UPDATE tournament_pairings SET status='completed',game_id=? WHERE id=?",(game.lastrowid,pairing['id']))
            db.execute("UPDATE tournaments SET status='completed' WHERE id=?",(child_id,))
        league.finalize_late_admission_batch(child_id)
        server._validate_tournament_checkpoint(self.parent['id'],self.checkpoint,self.pairing,
            self.players[self.pairing['white_model_id']],self.players[self.pairing['black_model_id']])


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
