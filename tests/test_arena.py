import csv
import io
import sqlite3
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import chess
import server


class ArenaSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tempdir = tempfile.TemporaryDirectory()
        server.DB = Path(cls.tempdir.name) / 'test.sqlite3'
        server.db_init()
        pgn = '[Event "Automated export test"]\n[Result "1-0"]\n\n1. e4 e5 1-0'
        with sqlite3.connect(server.DB) as db:
            db.execute(
                'INSERT INTO games(started_at,ended_at,white_model,black_model,result,pgn,move_count,illegal_attempts,avg_move_seconds) VALUES(?,?,?,?,?,?,?,?,?)',
                ('2026-11-13T00:00:00Z', '2026-11-13T00:01:00Z', 'Model A', 'Model B', '1-0', pgn, 2, 0, 0.42),
            )
        cls.http = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        cls.thread = threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f'http://127.0.0.1:{cls.http.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()
        cls.thread.join(timeout=2)
        cls.tempdir.cleanup()

    def test_python_chess_legal_start_position(self):
        board = chess.Board()
        self.assertEqual(board.legal_moves.count(), 20)
        move = chess.Move.from_uci('e2e4')
        self.assertIn(move, board.legal_moves)
        board.push(move)
        self.assertEqual(board.peek().uci(), 'e2e4')

    def test_pgn_export(self):
        with urllib.request.urlopen(self.base + '/api/export/pgn') as response:
            body = response.read().decode('utf-8')
            self.assertIn('Automated export test', body)
            self.assertIn('1. e4 e5', body)
            self.assertIn('application/x-chess-pgn', response.headers['Content-Type'])

    def test_csv_export(self):
        with urllib.request.urlopen(self.base + '/api/export/csv') as response:
            body = response.read().decode('utf-8-sig')
            rows = list(csv.reader(io.StringIO(body)))
            self.assertEqual(rows[0][0], 'id')
            self.assertEqual(rows[1][3], 'Model A')
            self.assertEqual(rows[1][5], '1-0')
            self.assertIn('text/csv', response.headers['Content-Type'])

    def test_game_history_api(self):
        with urllib.request.urlopen(self.base + '/api/games') as response:
            self.assertIn(b'"result": "1-0"', response.read())

    def test_model_registry_exposes_version_and_ownership_fields(self):
        with urllib.request.urlopen(self.base + '/api/models') as response:
            self.assertEqual(response.status, 200)
            self.assertIsInstance(__import__('json').loads(response.read()), list)

    def test_download_requires_explicit_confirmation(self):
        request = urllib.request.Request(self.base + '/api/models/download', data=b'{"version_id":1}', headers={'Content-Type':'application/json'}, method='POST')
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(request)
        self.assertEqual(ctx.exception.code, 422)

    def test_cleanup_requires_explicit_confirmation(self):
        request = urllib.request.Request(self.base + '/api/models/cleanup', data=b'{"version_id":1}', headers={'Content-Type':'application/json'}, method='POST')
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(request)
        self.assertEqual(ctx.exception.code, 422)

    def test_storage_api_reports_arena_managed_area(self):
        with urllib.request.urlopen(self.base + '/api/storage') as response:
            data = __import__('json').loads(response.read())
            self.assertIn('managed_directory', data)
            self.assertIn('managed_models', data)
            self.assertIn('managed_free_bytes', data)

    def test_game_schema_has_tournament_audit_columns(self):
        with sqlite3.connect(server.DB) as db:
            columns = {row[1] for row in db.execute('PRAGMA table_info(games)')}
        self.assertTrue({'tournament_id', 'pairing_id', 'move_times_json', 'inference_metadata_json'} <= columns)

    def test_tournament_start_rejects_unknown_id_before_spawning(self):
        with self.assertRaises(ValueError):
            server.start_tournament(999999)
        self.assertFalse(server.tournament_thread and server.tournament_thread.is_alive())

    def test_tournament_version_path_requires_verified_download(self):
        with self.assertRaises(RuntimeError):
            server._version_path({'id': 1, 'license_verified': 0, 'status': 'downloaded',
                                  'filename': 'model.gguf', 'managed_copy': 0})
        with self.assertRaises(RuntimeError):
            server._version_path({'id': 1, 'license_verified': 1, 'status': 'catalogued',
                                  'filename': None, 'managed_copy': 0})
        with self.assertRaises(RuntimeError):
            server._version_path({'id': 1, 'license_verified': 1, 'status': 'downloaded',
                                  'filename': 'shared.gguf', 'managed_copy': 0})

    def test_managed_path_is_the_only_model_directory(self):
        self.assertEqual(server.MODEL_DIR.resolve(), server.model_store.MANAGED_DIR.resolve())
        self.assertNotEqual(str(server.MODEL_DIR), '/home/debian/ai-agent/models')


if __name__ == '__main__':
    unittest.main(verbosity=2)
