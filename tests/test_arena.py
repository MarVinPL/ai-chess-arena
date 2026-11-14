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

    def test_lazy_runtime_preflight_checks_executable_not_running_health(self):
        original_binary = server.engine_runner.LLAMAFILE
        try:
            with tempfile.TemporaryDirectory() as directory:
                binary = Path(directory) / 'llamafile'
                binary.write_text('#!/bin/sh\nexit 0\n', encoding='utf-8')
                binary.chmod(0o755)
                server.engine_runner.LLAMAFILE = binary
                self.assertTrue(server.llm_runtime_available())
                binary.chmod(0o644)
                self.assertFalse(server.llm_runtime_available())
                binary.unlink()
                self.assertFalse(server.llm_runtime_available())
        finally:
            server.engine_runner.LLAMAFILE = original_binary

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

    def test_runtime_model_labels_resolve_to_registry_names(self):
        names = {'qwen2-0_5b-instruct-q4_k_m.gguf': 'Qwen2-0.5B-Instruct-Q4_K_M'}
        self.assertEqual(
            server._registry_display_name_for_runtime_model(
                '/models/qwen2-0_5b-instruct-q4_k_m.gguf', names),
            'Qwen2-0.5B-Instruct-Q4_K_M',
        )
        self.assertIsNone(server._registry_display_name_for_runtime_model(
            'unknown.gguf', names))
        self.assertEqual(
            server._registry_runtime_message(
                'Ładowanie modelu: qwen2-0_5b-instruct-q4_k_m.gguf', names),
            'Ładowanie modelu: Qwen2-0.5B-Instruct-Q4_K_M',
        )
        self.assertEqual(
            server._registry_runtime_message('Model loading', names),
            'Model loading',
        )

    def test_pairing_resolves_registry_names_for_both_color_assignments(self):
        players = {
            3: {'model_id': 3, 'name': 'Qwen2-0.5B-Instruct-Q4_K_M'},
            2: {'model_id': 2, 'name': 'SmolLM2-360M-Instruct-Q4_K_M'},
        }
        white, black = server._resolve_pairing_players(
            players, {'white_model_id': 3, 'black_model_id': 2})
        self.assertEqual((white['name'], black['name']),
                         ('Qwen2-0.5B-Instruct-Q4_K_M', 'SmolLM2-360M-Instruct-Q4_K_M'))
        white, black = server._resolve_pairing_players(
            players, {'white_model_id': 2, 'black_model_id': 3})
        self.assertEqual((white['name'], black['name']),
                         ('SmolLM2-360M-Instruct-Q4_K_M', 'Qwen2-0.5B-Instruct-Q4_K_M'))
        with self.assertRaises(RuntimeError):
            server._resolve_pairing_players(
                players, {'white_model_id': 3, 'black_model_id': 3})

    def test_live_logs_persist_model_responses_and_recent_events(self):
        server.set_live('move_generated', 'Model zaproponował ruch: e2e4',
                        white='Registry Model A', black='Registry Model B',
                        model='Registry Model A', color='white', response='e2e4',
                        proposed_move='e2e4', elapsed_seconds=1.25)
        self.assertTrue(server.MODEL_RESPONSES_LOG.is_file())
        self.assertTrue(server.LIVE_EVENTS_LOG.is_file())
        response_record = __import__('json').loads(server.MODEL_RESPONSES_LOG.read_text(encoding='utf-8').splitlines()[-1])
        event_record = __import__('json').loads(server.LIVE_EVENTS_LOG.read_text(encoding='utf-8').splitlines()[-1])
        self.assertEqual(response_record['response'], 'e2e4')
        self.assertEqual(response_record['model'], 'Registry Model A')
        self.assertEqual(event_record['stage'], 'move_generated')
        self.assertEqual(event_record['proposed_move'], 'e2e4')
        self.assertEqual(event_record['white'], 'Registry Model A')
        self.assertEqual(event_record['black'], 'Registry Model B')

    def test_live_status_keeps_pairing_stage_separate_from_runtime_stage(self):
        server.set_live('game_finished', 'Partia zakończona: 1-0',
                        white='Model A', black='Model B', pairing_stage='league')
        self.assertEqual(server.live_status['stage'], 'game_finished')
        self.assertEqual(server.live_status['pairing_stage'], 'league')
        event_record = __import__('json').loads(
            server.LIVE_EVENTS_LOG.read_text(encoding='utf-8').splitlines()[-1])
        self.assertEqual(event_record['stage'], 'game_finished')
        self.assertEqual(event_record['pairing_stage'], 'league')

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
            payload = __import__('json').loads(response.read())
        self.assertEqual(payload[0]['white'], 'Model A')
        self.assertEqual(payload[0]['black'], 'Model B')
        self.assertEqual(payload[0]['result'], '1-0')

    def test_game_detail_exposes_arbiter_intervention_audit(self):
        move_metadata = [{
            'ply': 12, 'color': 'white', 'model': 'Arbiter Test',
            'arbiter_intervention': True,
            'arbiter_signal': {'type': 'piece_shuttle', 'reason': 'repeated shuttle'},
            'arbiter_response': 'g1f3', 'seconds': 0.42, 'uci': 'g1f3',
        }]
        with sqlite3.connect(server.DB) as db:
            cur = db.execute(
                "INSERT INTO games(started_at,ended_at,white_model,black_model,result,pgn,move_count,illegal_attempts,avg_move_seconds,inference_metadata_json) VALUES(?,?,?,?,?,?,?,?,?,?)",
                ('now', 'now', 'Arbiter Test', 'Opponent', '1/2-1/2', '[Result \"1/2-1/2\"]',
                 1, 0, 0.42, __import__('json').dumps(move_metadata)),
            )
            game_id = cur.lastrowid
        try:
            with urllib.request.urlopen(self.base + f'/api/games/{game_id}') as response:
                payload = __import__('json').loads(response.read())
            self.assertEqual(payload['arbiter_events'][0]['signal']['type'], 'piece_shuttle')
            self.assertEqual(payload['arbiter_events'][0]['response_seconds'], 0.42)
            self.assertEqual(payload['arbiter_events'][0]['selected_move'], 'g1f3')
        finally:
            with sqlite3.connect(server.DB) as db:
                db.execute('DELETE FROM games WHERE id=?', (game_id,))

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

    def test_tournament_finalizer_releases_model_lock_if_runner_stop_fails(self):
        class BrokenRuntime:
            def stop(self):
                raise RuntimeError('simulated runner shutdown failure')

        old_runtime = server.tournament_runtime
        old_error = server.tournament_error
        server.tournament_runtime = BrokenRuntime()
        try:
            worker = threading.Thread(target=server.tournament_loop, args=(-1,))
            worker.start()
            worker.join(timeout=3)
            self.assertFalse(worker.is_alive())
            lock = server.model_store.MODEL_LIFECYCLE_LOCK
            self.assertTrue(lock.acquire(blocking=False))
            lock.release()
            self.assertIn('Runner shutdown failed', server.tournament_error)
        finally:
            server.tournament_runtime = old_runtime
            server.tournament_error = old_error

    def test_cleanup_requires_explicit_confirmation(self):
        request = urllib.request.Request(self.base + '/api/models/cleanup', data=b'{"version_id":1}', headers={'Content-Type':'application/json'}, method='POST')
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(request)
        self.assertEqual(ctx.exception.code, 422)

    def test_cleanup_is_rejected_while_tournament_holds_model_lifecycle_lock(self):
        with server.model_store.MODEL_LIFECYCLE_LOCK:
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                self._post_json('/api/models/cleanup', {'version_id': 1, 'confirm_delete': True})
            self.assertEqual(ctx.exception.code, 422)
            self.assertIn('aktywnego turnieju', ctx.exception.read().decode())

    def test_storage_api_reports_arena_managed_area(self):
        with urllib.request.urlopen(self.base + '/api/storage') as response:
            data = __import__('json').loads(response.read())
            self.assertIn('managed_directory', data)
            self.assertIn('managed_models', data)
            self.assertIn('managed_free_bytes', data)

    def test_game_schema_has_tournament_and_adjudication_audit_columns(self):
        with sqlite3.connect(server.DB) as db:
            columns = {row[1] for row in db.execute('PRAGMA table_info(games)')}
        self.assertTrue({
            'tournament_id', 'pairing_id', 'move_times_json', 'inference_metadata_json',
            'adjudication_status', 'adjudication_json', 'failure_reason',
            'failure_fen', 'failure_color', 'failure_side_to_move', 'failure_model_id'
        } <= columns)

    def test_failed_game_is_saved_and_only_adjudicated_result_counts_for_elo(self):
        failure = RuntimeError('synthetic')
        failure.move_records = []
        failure.move_times = []
        failure.uci_attempts = []
        failure.fen = chess.Board().fen()
        failure.color = 'white'
        failure.model_id = 1
        with sqlite3.connect(server.DB) as db:
            db.execute("INSERT INTO league_models(name,format,created_at) VALUES(?,?,?)",
                       ('Adjudication White', 'PURE_AI', 'now'))
            db.execute("INSERT INTO league_models(name,format,created_at) VALUES(?,?,?)",
                       ('Adjudication Black', 'PURE_AI', 'now'))
            white_id = db.execute("SELECT id FROM league_models WHERE name='Adjudication White'").fetchone()[0]
            black_id = db.execute("SELECT id FROM league_models WHERE name='Adjudication Black'").fetchone()[0]
            pairings = []
            for suffix in ('unresolved', 'adjudicated'):
                cur = db.execute("INSERT INTO tournaments(name,format,status,created_at) VALUES(?,?,?,?)",
                                 ('Adjudication ' + suffix, 'PURE_AI', 'running', 'now'))
                tournament_id = cur.lastrowid
                cur = db.execute("INSERT INTO tournament_entries(tournament_id,model_id) VALUES(?,?)",
                                 (tournament_id, white_id))
                db.execute("INSERT INTO tournament_entries(tournament_id,model_id) VALUES(?,?)",
                           (tournament_id, black_id))
                cur = db.execute("INSERT INTO tournament_pairings(tournament_id,white_model_id,black_model_id,leg,status,stage) VALUES(?,?,?,?,?,?)",
                                 (tournament_id, white_id, black_id, 1, 'pending', 'league'))
                pairings.append({'id': cur.lastrowid, 'stage': 'league', 'tournament_id': tournament_id})
        white = {'model_id': white_id, 'name': 'Adjudication White'}
        black = {'model_id': black_id, 'name': 'Adjudication Black'}
        failure.model_id = white_id
        unresolved = server._persist_failed_game(
            pairings[0]['tournament_id'], pairings[0], white, black, 'start', failure,
            {'status': 'unresolved', 'reason': 'analysis_engine_not_configured'})
        adjudicated = server._persist_failed_game(
            pairings[1]['tournament_id'], pairings[1], white, black, 'start', failure,
            {'status': 'adjudicated', 'winner_color': 'black', 'reason': 'stable_decisive_advantage'})
        self.assertEqual(unresolved['result'], 'failed')
        self.assertFalse(unresolved['adjudicated'])
        self.assertEqual(adjudicated['result'], '0-1')
        self.assertTrue(adjudicated['adjudicated'])
        with sqlite3.connect(server.DB) as db:
            failed = db.execute("SELECT result,adjudication_status,failure_reason,failure_fen,failure_side_to_move FROM games WHERE id=?",
                                (unresolved['game_id'],)).fetchone()
            completed = db.execute("SELECT result,adjudication_status FROM games WHERE id=?",
                                   (adjudicated['game_id'],)).fetchone()
            pairing = db.execute("SELECT status,winner_model_id FROM tournament_pairings WHERE id=?",
                                 (pairings[1]['id'],)).fetchone()
        self.assertEqual(failed[0:2], ('failed', 'unresolved'))
        self.assertEqual(failed[2], 'synthetic')
        self.assertEqual(failed[3], chess.Board().fen())
        self.assertEqual(failed[4], 'white')
        self.assertEqual(completed, ('0-1', 'adjudicated'))
        self.assertEqual(pairing, ('completed', black_id))
        ratings = {row['name']: row for row in server.league.elo_ratings('PURE_AI')}
        self.assertEqual((ratings['Adjudication White']['games'], ratings['Adjudication White']['losses']), (1, 1))
        self.assertEqual((ratings['Adjudication Black']['games'], ratings['Adjudication Black']['wins']), (1, 1))
        with sqlite3.connect(server.DB) as db:
            tournament_ids = [item['tournament_id'] for item in pairings]
            placeholders = ','.join('?' for _ in tournament_ids)
            db.execute(f"DELETE FROM games WHERE tournament_id IN ({placeholders})", tournament_ids)
            db.execute(f"DELETE FROM tournament_pairings WHERE tournament_id IN ({placeholders})", tournament_ids)
            db.execute(f"DELETE FROM tournament_entries WHERE tournament_id IN ({placeholders})", tournament_ids)
            db.execute(f"DELETE FROM tournaments WHERE id IN ({placeholders})", tournament_ids)
            db.execute("DELETE FROM league_models WHERE id IN (?,?)", (white_id, black_id))


    def test_tournament_api_reports_only_saved_completed_pairing_games(self):
        statuses = ('planned', 'running', 'stopped', 'failed', 'completed')
        tournament_ids = []
        with sqlite3.connect(server.DB) as db:
            db.execute("INSERT INTO league_models(name,format,created_at) VALUES(?,?,?)",
                       ('Progress White', 'PURE_AI', 'now'))
            db.execute("INSERT INTO league_models(name,format,created_at) VALUES(?,?,?)",
                       ('Progress Black', 'PURE_AI', 'now'))
            white_id = db.execute("SELECT id FROM league_models WHERE name='Progress White'").fetchone()[0]
            black_id = db.execute("SELECT id FROM league_models WHERE name='Progress Black'").fetchone()[0]
            for status in statuses:
                cur = db.execute(
                    "INSERT INTO tournaments(name,format,status,created_at,rules_version,qualification_percent) VALUES(?,?,?,?,?,?)",
                    (f'Progress counter {status}', 'PURE_AI', status,
                     '2026-11-14T00:00:00Z', '1', 80),
                )
                tournament_id = cur.lastrowid
                tournament_ids.append(tournament_id)
                cur = db.execute(
                    "INSERT INTO tournament_pairings(tournament_id,white_model_id,black_model_id,leg,status,stage) VALUES(?,?,?,?,?,?)",
                    (tournament_id, white_id, black_id, 1, 'completed', 'league'),
                )
                completed_pairing_id = cur.lastrowid
                cur = db.execute(
                    "INSERT INTO games(started_at,ended_at,white_model,black_model,result,pgn,move_count,illegal_attempts,avg_move_seconds,tournament_id,pairing_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    ('2026-11-14T00:00:00Z', '2026-11-14T00:01:00Z',
                     'Progress White', 'Progress Black', '1-0', '[Result "1-0"]',
                     1, 0, 0.5, tournament_id, completed_pairing_id),
                )
                db.execute("UPDATE tournament_pairings SET game_id=? WHERE id=?",
                           (cur.lastrowid, completed_pairing_id))
                cur = db.execute(
                    "INSERT INTO tournament_pairings(tournament_id,white_model_id,black_model_id,leg,status,stage) VALUES(?,?,?,?,?,?)",
                    (tournament_id, black_id, white_id, 1, 'running', 'league'),
                )
                running_pairing_id = cur.lastrowid
                cur = db.execute(
                    "INSERT INTO games(started_at,ended_at,white_model,black_model,result,pgn,move_count,illegal_attempts,avg_move_seconds,tournament_id,pairing_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    ('2026-11-14T00:02:00Z', '2026-11-14T00:03:00Z',
                     'Progress Black', 'Progress White', '0-1', '[Result "0-1"]',
                     1, 0, 0.5, tournament_id, running_pairing_id),
                )
                db.execute("UPDATE tournament_pairings SET game_id=? WHERE id=?",
                           (cur.lastrowid, running_pairing_id))
            cur = db.execute(
                "INSERT INTO tournaments(name,format,status,created_at,rules_version,qualification_percent) VALUES(?,?,?,?,?,?)",
                ('Progress counter empty', 'PURE_AI', 'planned',
                 '2026-11-14T00:00:00Z', '1', 80),
            )
            empty_tournament_id = cur.lastrowid

        with urllib.request.urlopen(self.base + '/api/tournaments') as response:
            rows = __import__('json').loads(response.read())
        by_id = {row['id']: row for row in rows}
        for tournament_id in tournament_ids:
            self.assertEqual(by_id[tournament_id]['completed_games'], 1)
            self.assertEqual(by_id[tournament_id]['total_games_planned'], 2)
        self.assertEqual(by_id[empty_tournament_id]['completed_games'], 0)
        self.assertEqual(by_id[empty_tournament_id]['total_games_planned'], 0)

    def test_tournament_view_api_and_saved_pgn_preview_are_read_only(self):
        with sqlite3.connect(server.DB) as db:
            db.execute("INSERT INTO league_models(name,format,created_at) VALUES('View API Alpha','PURE_AI','now')")
            alpha = db.execute("SELECT id FROM league_models WHERE name='View API Alpha'").fetchone()[0]
            db.execute("INSERT INTO league_models(name,format,created_at) VALUES('View API Beta','PURE_AI','now')")
            beta = db.execute("SELECT id FROM league_models WHERE name='View API Beta'").fetchone()[0]
            cur = db.execute("INSERT INTO tournaments(name,format,status,created_at) VALUES('Read-only API view','PURE_AI','running','now')")
            tournament_id = cur.lastrowid
            db.executemany("INSERT INTO tournament_entries(tournament_id,model_id) VALUES(?,?)", [(tournament_id,alpha),(tournament_id,beta)])
            cur = db.execute("INSERT INTO tournament_pairings(tournament_id,white_model_id,black_model_id,leg,status,stage) VALUES(?,?,?,?,?,?)",
                             (tournament_id,alpha,beta,1,'completed','league'))
            pairing_id = cur.lastrowid
            pgn = '[Event "Read-only preview"]\\n[Result "1-0"]\\n\\n1. e4 1-0'
            cur = db.execute("INSERT INTO games(started_at,ended_at,white_model,black_model,result,pgn,move_count,illegal_attempts,avg_move_seconds,tournament_id,pairing_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                             ('now','now','View API Alpha','View API Beta','1-0',pgn,1,0,0.2,tournament_id,pairing_id))
            game_id = cur.lastrowid
            db.execute("UPDATE tournament_pairings SET game_id=? WHERE id=?", (game_id,pairing_id))
        with urllib.request.urlopen(self.base + f'/api/tournaments/{tournament_id}/view') as response:
            view = __import__('json').loads(response.read().decode('utf-8'))
        self.assertEqual(view['tournament']['id'], tournament_id)
        self.assertEqual(view['progress']['league'], {'completed':1,'planned':1})
        self.assertEqual(view['standings'][0]['points'], 3)
        self.assertEqual(view['fixtures'][0]['game_url'], f'/api/games/{game_id}/pgn')
        with urllib.request.urlopen(self.base + f'/api/games/{game_id}/pgn') as response:
            self.assertIn('Read-only preview', response.read().decode('utf-8'))
            self.assertIn('application/x-chess-pgn', response.headers['Content-Type'])
        with sqlite3.connect(server.DB) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM tournament_seeds WHERE tournament_id=?", (tournament_id,)).fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT status FROM tournaments WHERE id=?", (tournament_id,)).fetchone()[0], 'running')

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
