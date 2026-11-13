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
        server.LOG_DIR = Path(cls.tempdir.name) / 'logs'
        server.MODEL_RESPONSES_LOG = server.LOG_DIR / 'model-responses.jsonl'
        server.LIVE_EVENTS_LOG = server.LOG_DIR / 'recent-events.jsonl'
        cls.old_league_db = server.league.DB
        server.league.DB = server.DB
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
        server.league.DB = cls.old_league_db
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

    def test_live_logs_persist_model_responses_and_recent_events(self):
        server.set_live('move_generated', 'Model zaproponował ruch: e2e4',
                        model='TestModel.gguf', color='white', response='e2e4',
                        proposed_move='e2e4', elapsed_seconds=1.25)
        self.assertTrue(server.MODEL_RESPONSES_LOG.is_file())
        self.assertTrue(server.LIVE_EVENTS_LOG.is_file())
        response_record = __import__('json').loads(server.MODEL_RESPONSES_LOG.read_text(encoding='utf-8').splitlines()[-1])
        event_record = __import__('json').loads(server.LIVE_EVENTS_LOG.read_text(encoding='utf-8').splitlines()[-1])
        self.assertEqual(response_record['response'], 'e2e4')
        self.assertEqual(response_record['model'], 'TestModel.gguf')
        self.assertEqual(event_record['stage'], 'move_generated')
        self.assertEqual(event_record['proposed_move'], 'e2e4')

    def test_diagnostic_logs_apply_separate_age_retention(self):
        import json
        from datetime import datetime, timedelta, timezone
        server.LOG_DIR.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc)
        expired_response = (now - timedelta(days=4)).strftime('%Y-%m-%dT%H:%M:%SZ')
        recent_response = (now - timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
        expired_event = (now - timedelta(days=8)).strftime('%Y-%m-%dT%H:%M:%SZ')
        recent_event = (now - timedelta(days=2)).strftime('%Y-%m-%dT%H:%M:%SZ')
        server.MODEL_RESPONSES_LOG.write_text('\n'.join([
            json.dumps({'at': expired_response, 'response': 'old'}),
            json.dumps({'at': recent_response, 'response': 'new'}),
        ]) + '\n', encoding='utf-8')
        server.LIVE_EVENTS_LOG.write_text('\n'.join([
            json.dumps({'at': expired_event, 'message': 'old'}),
            json.dumps({'at': recent_event, 'message': 'new'}),
        ]) + '\n', encoding='utf-8')
        responses = server._read_jsonl(server.MODEL_RESPONSES_LOG, 200, server.MODEL_RESPONSE_RETENTION_SECONDS)
        events = server._read_jsonl(server.LIVE_EVENTS_LOG, 500, server.LIVE_EVENT_RETENTION_SECONDS)
        self.assertEqual([item['response'] for item in responses], ['new'])
        self.assertEqual([item['message'] for item in events], ['new'])

    def test_live_api_restores_logs_when_memory_history_is_empty(self):
        server.set_live('move_generated', 'Model zaproponował ruch: d2d4',
                        model='RestartTest.gguf', response='d2d4', proposed_move='d2d4')
        with server.live_lock:
            server.live_status['events'] = []
            server.live_status['response'] = None
        with urllib.request.urlopen(self.base + '/api/live') as response:
            payload = __import__('json').loads(response.read())
        self.assertEqual(payload['response'], 'd2d4')
        self.assertEqual(payload['events'][0]['stage'], 'move_generated')

    def test_game_history_api(self):
        with urllib.request.urlopen(self.base + '/api/games') as response:
            self.assertIn(b'"result": "1-0"', response.read())

    def test_model_registry_exposes_version_and_ownership_fields(self):
        with urllib.request.urlopen(self.base + '/api/models') as response:
            self.assertEqual(response.status, 200)
            self.assertIsInstance(__import__('json').loads(response.read()), list)

    def _post_json(self, path, payload):
        request = urllib.request.Request(
            self.base + path,
            data=__import__('json').dumps(payload).encode(),
            headers={'Content-Type': 'application/json'},
            method='POST',
        )
        return urllib.request.urlopen(request)

    def test_license_verification_requires_explicit_confirmation(self):
        with sqlite3.connect(server.DB) as db:
            db.execute("INSERT INTO league_models(name,format,created_at) VALUES('License Test','PURE_AI','now')")
            db.execute("INSERT INTO model_versions(model_id,version_key,source,license,created_at) VALUES((SELECT id FROM league_models WHERE name='License Test'),'license-test','https://huggingface.co/example/model','MIT','now')")
            version_id = db.execute("SELECT id FROM model_versions WHERE version_key='license-test'").fetchone()[0]
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._post_json('/api/models/verify-license', {'version_id': version_id})
        self.assertEqual(ctx.exception.code, 422)
        with sqlite3.connect(server.DB) as db:
            self.assertEqual(db.execute('SELECT license_verified FROM model_versions WHERE id=?', (version_id,)).fetchone()[0], 0)

    def test_license_verification_marks_only_selected_version(self):
        with sqlite3.connect(server.DB) as db:
            db.execute("INSERT INTO league_models(name,format,created_at) VALUES('License Approved','PURE_AI','now')")
            model_id = db.execute("SELECT id FROM league_models WHERE name='License Approved'").fetchone()[0]
            db.execute("INSERT INTO model_versions(model_id,version_key,source,license,created_at) VALUES(?,?,?,?,?)", (model_id, 'approved-test', 'https://huggingface.co/example/approved', 'Apache-2.0', 'now'))
            db.execute("INSERT INTO model_versions(model_id,version_key,source,license,created_at) VALUES(?,?,?,?,?)", (model_id, 'sibling-test', 'https://huggingface.co/example/sibling', 'MIT', 'now'))
            version_id = db.execute("SELECT id FROM model_versions WHERE version_key='approved-test'").fetchone()[0]
            sibling_id = db.execute("SELECT id FROM model_versions WHERE version_key='sibling-test'").fetchone()[0]
        with self._post_json('/api/models/verify-license', {'version_id': version_id, 'confirm_review': True}) as response:
            data = __import__('json').loads(response.read())
        self.assertEqual(response.status, 200)
        self.assertEqual(data, {'version_id': version_id, 'license_verified': True})
        with sqlite3.connect(server.DB) as db:
            self.assertEqual(db.execute('SELECT license_verified FROM model_versions WHERE id=?', (version_id,)).fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT license_verified FROM model_versions WHERE id=?', (sibling_id,)).fetchone()[0], 0)

    def test_license_verification_rejects_unknown_license(self):
        with sqlite3.connect(server.DB) as db:
            db.execute("INSERT INTO league_models(name,format,created_at) VALUES('Unknown License','PURE_AI','now')")
            db.execute("INSERT INTO model_versions(model_id,version_key,source,license,created_at) VALUES((SELECT id FROM league_models WHERE name='Unknown License'),'unknown-license','https://huggingface.co/example/unknown','nieustalona','now')")
            version_id = db.execute("SELECT id FROM model_versions WHERE version_key='unknown-license'").fetchone()[0]
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._post_json('/api/models/verify-license', {'version_id': version_id, 'confirm_review': True})
        self.assertEqual(ctx.exception.code, 422)
        with sqlite3.connect(server.DB) as db:
            self.assertEqual(db.execute('SELECT license_verified FROM model_versions WHERE id=?', (version_id,)).fetchone()[0], 0)

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
