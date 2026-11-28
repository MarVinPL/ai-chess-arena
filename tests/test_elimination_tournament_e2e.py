"""Isolated server-loop integration test for an Elimination Chess tournament."""
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import chess
import engine_runner
import league
import server
from elimination_chess import EliminationBoard


class KingCaptureRuntime:
    def __init__(self):
        self.calls = []
        self.stop_calls = 0
        self.shutdown_calls = 0

    def generate(self, model_path, board, stop_event=None, **kwargs):
        self.calls.append({
            'model_path': str(model_path),
            'is_elimination_board': isinstance(board, EliminationBoard),
            'fen': board.fen(),
        })
        return chess.Move.from_uci('e2e8'), {'seconds': 0.01, 'budget_observations': []}

    def stop(self):
        self.stop_calls += 1

    def shutdown(self):
        self.shutdown_calls += 1


class EliminationTournamentEndToEndTests(unittest.TestCase):
    def test_server_tournament_persists_king_capture_result_and_variant_pgn(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'arena-e2e.sqlite3'
            previous = {
                'server_db': server.DB,
                'league_db': league.DB,
                'runtime': server.tournament_runtime,
                'stop_event': server.tournament_stop_event,
                'error': server.tournament_error,
                'live_status': dict(server.live_status),
            }
            server.DB = db_path
            league.DB = db_path
            server.tournament_runtime = None
            server.tournament_stop_event = threading.Event()
            server.tournament_error = ''
            try:
                server.db_init()
                for name, digest in (('Alpha', 'a' * 64), ('Beta', 'b' * 64)):
                    league.register_model(
                        name,
                        source=f'https://example.test/{name}.gguf',
                        license='Apache-2.0',
                        license_verified=True,
                        metadata={
                            'filename': name.lower() + '.gguf',
                            'sha256': digest,
                            'size_bytes': 100,
                            'source_path': name.lower() + '.gguf',
                            'download_url': f'https://example.test/{name}.gguf',
                            'inference': {'thinking': False, 'temperature': 0, 'max_tokens': 16},
                        },
                    )
                tournament = league.create_round_robin('E2E Elimination Chess')
                tournament_id = tournament['id']
                with league.connect() as db:
                    # One fixture keeps the integration test focused on the full
                    # tournament -> checkpoint -> runner -> persistence lifecycle.
                    db.execute("UPDATE tournaments SET rules_version='1' WHERE id=?", (tournament_id,))
                    db.execute(
                        'DELETE FROM tournament_pairings WHERE tournament_id=? AND id=('
                        'SELECT MAX(id) FROM tournament_pairings WHERE tournament_id=?)',
                        (tournament_id, tournament_id),
                    )

                initial_fen = '4k3/8/8/8/8/8/4R3/4K3 w - - 0 1'
                runtime = KingCaptureRuntime()
                with (
                    patch.object(server, 'tournament_preflight', return_value={
                        'ready': True, 'entrants': [], 'runtime_available': True,
                    }),
                    patch.object(server.runtime_factory, 'create_runtime', return_value=runtime),
                    patch.object(server.passkey_test, 'questions_for_tournament', return_value=[]),
                    patch.object(server, '_prepare_game_models', return_value=[
                        {'path': '/tmp/alpha.gguf'}, {'path': '/tmp/beta.gguf'},
                    ]),
                    patch.object(server, 'cleanup_unneeded_models', return_value=[]),
                    patch.object(server.league, 'finalize_late_admission_batch', return_value=None),
                    patch.object(server, 'EliminationBoard',
                                 return_value=EliminationBoard(initial_fen)),
                    patch.object(engine_runner, 'EliminationBoard',
                                 return_value=EliminationBoard(initial_fen)),
                ):
                    server.tournament_loop(tournament_id)

                with league.connect() as db:
                    tournament_status = db.execute(
                        'SELECT status FROM tournaments WHERE id=?', (tournament_id,)
                    ).fetchone()['status']
                    pairing = db.execute(
                        'SELECT status,game_id FROM tournament_pairings WHERE tournament_id=?',
                        (tournament_id,),
                    ).fetchone()
                    game = db.execute(
                        'SELECT result,pgn,move_count FROM games WHERE tournament_id=?',
                        (tournament_id,),
                    ).fetchone()
                    checkpoint_count = db.execute(
                        'SELECT COUNT(*) FROM tournament_checkpoints WHERE tournament_id=?',
                        (tournament_id,),
                    ).fetchone()[0]

                self.assertEqual(tournament_status, 'completed')
                self.assertEqual(pairing['status'], 'completed')
                self.assertIsNotNone(pairing['game_id'])
                self.assertEqual(game['result'], '1-0')
                self.assertEqual(game['move_count'], 1)
                self.assertIn('[Variant "Elimination Chess"]', game['pgn'])
                self.assertIn('Rxe8', game['pgn'])
                self.assertEqual(checkpoint_count, 0)
                self.assertEqual(len(runtime.calls), 1)
                self.assertTrue(runtime.calls[0]['is_elimination_board'])
                self.assertEqual(runtime.calls[0]['fen'], initial_fen)
                self.assertEqual(runtime.stop_calls, 1)
                self.assertEqual(runtime.shutdown_calls, 1)
                self.assertEqual(server.tournament_error, '')
            finally:
                server.DB = previous['server_db']
                league.DB = previous['league_db']
                server.tournament_runtime = previous['runtime']
                server.tournament_stop_event = previous['stop_event']
                server.tournament_error = previous['error']
                server.live_status.clear()
                server.live_status.update(previous['live_status'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
