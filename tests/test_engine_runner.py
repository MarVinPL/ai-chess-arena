import threading
import unittest
import chess
from engine_runner import SequentialChessRunner, RunnerError


class FakeRuntime:
    def __init__(self):
        self.calls = []
    def generate(self, model_path, board, stop_event=None, recent_moves=None):
        if stop_event and stop_event.is_set():
            raise RunnerError('stopped')
        move = next(iter(board.legal_moves))
        self.calls.append((str(model_path), board.turn, move.uci()))
        return move, {'seconds': 0.01, 'runtime': 'fake'}


class EngineRunnerTests(unittest.TestCase):
    def test_models_alternate_and_each_side_uses_own_identity(self):
        runtime = FakeRuntime()
        runner = SequentialChessRunner(runtime)
        # Fool's mate from the standard position, supplied as deterministic fake moves.
        sequence = ['f2f3', 'e7e5', 'g2g4', 'd8h4']
        class FoolRuntime:
            def __init__(self): self.calls = []
            def generate(self, path, board, stop_event=None, recent_moves=None):
                uci = sequence[len(self.calls)]
                self.calls.append((str(path), board.turn))
                return chess.Move.from_uci(uci), {'seconds': 0.01}
        fake = FoolRuntime()
        result = SequentialChessRunner(fake).play_game(
            {'name': 'Alpha', 'path': '/tmp/alpha.gguf'},
            {'name': 'Beta', 'path': '/tmp/beta.gguf'})
        self.assertEqual(result['status'], 'finished')
        self.assertEqual(result['result'], '0-1')
        self.assertEqual([x[0] for x in fake.calls], ['/tmp/alpha.gguf', '/tmp/beta.gguf'] * 2)
        self.assertEqual([m['model'] for m in result['moves']], ['Alpha', 'Beta', 'Alpha', 'Beta'])
        self.assertIn('[White "Alpha"]', result['pgn'])
        self.assertIn('[Black "Beta"]', result['pgn'])

    def test_target_with_draft_is_one_player_configuration(self):
        sequence=['f2f3','e7e5','g2g4','d8h4']
        class PairRuntime:
            def __init__(self): self.calls=[]
            def generate(self,path,board,stop_event=None,draft_model_path=None,recent_moves=None):
                uci=sequence[len(self.calls)]
                self.calls.append((str(path),str(draft_model_path) if draft_model_path else None))
                return chess.Move.from_uci(uci),{'seconds':0.01}
        fake=PairRuntime()
        result=SequentialChessRunner(fake).play_game(
            {'name':'Alpha + MTP','path':'/tmp/alpha.gguf','draft_path':'/tmp/alpha-draft.gguf'},
            {'name':'Beta','path':'/tmp/beta.gguf'})
        self.assertEqual(result['status'],'finished')
        self.assertEqual(fake.calls,[('/tmp/alpha.gguf','/tmp/alpha-draft.gguf'),('/tmp/beta.gguf',None),
                                     ('/tmp/alpha.gguf','/tmp/alpha-draft.gguf'),('/tmp/beta.gguf',None)])

    def test_progress_callback_receives_each_validated_move_and_position(self):
        sequence = ['f2f3', 'e7e5', 'g2g4', 'd8h4']
        class FoolRuntime:
            def __init__(self): self.calls = []
            def generate(self, path, board, stop_event=None, recent_moves=None):
                uci = sequence[len(self.calls)]
                self.calls.append(uci)
                return chess.Move.from_uci(uci), {'seconds': 0.02}
        progress = []
        result = SequentialChessRunner(FoolRuntime()).play_game(
            {'name': 'Alpha', 'path': '/tmp/alpha.gguf'},
            {'name': 'Beta', 'path': '/tmp/beta.gguf'}, progress=progress.append)
        self.assertEqual(len(progress), 4)
        self.assertEqual([item['uci'] for item in progress], sequence)
        self.assertTrue(all(item.get('fen') for item in progress))
        self.assertEqual(progress[-1]['san'], 'Qh4#')
        self.assertEqual(progress[-1]['fen'], result['fen'])

    def test_stop_before_first_move_returns_stopped(self):
        event = threading.Event(); event.set()
        result = SequentialChessRunner(FakeRuntime()).play_game(
            {'name': 'A', 'path': '/tmp/a.gguf'},
            {'name': 'B', 'path': '/tmp/b.gguf'}, event)
        self.assertEqual(result['status'], 'stopped')
        self.assertEqual(result['moves'], [])

    def test_runtime_status_callback_is_optional_and_receives_events(self):
        from engine_runner import LlamafileRuntime
        events = []
        runtime = LlamafileRuntime(on_status=lambda stage, message, **fields: events.append((stage, message, fields)))
        runtime._notify('generating', 'Model generuje ruch', model='test.gguf', color='white')
        self.assertEqual(events[0][0], 'generating')
        self.assertEqual(events[0][2]['model'], 'test.gguf')
        self.assertEqual(events[0][2]['color'], 'white')

    def test_llamafile_receives_mtp_draft_flags_for_pair_configuration(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from engine_runner import LlamafileRuntime
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); binary=root/'llamafile'; target=root/'target.gguf'; draft=root/'draft.gguf'
            binary.write_text('#!/bin/sh\n'); binary.chmod(0o700)
            target.write_bytes(b'target'); draft.write_bytes(b'draft')
            calls=[]
            class Process:
                returncode=None
                def poll(self): return None
                def terminate(self): pass
                def wait(self,timeout=None): return 0
                def kill(self): pass
            runtime=LlamafileRuntime(binary=binary,popen=lambda args,**kwargs:(calls.append(args) or Process()))
            with patch.object(runtime,'_healthy',return_value=True):
                runtime.ensure_loaded(target,draft)
            args=calls[0]
            self.assertIn('--spec-type',args); self.assertIn('draft-mtp',args)
            self.assertIn('--model-draft',args); self.assertIn(str(draft.resolve()),args)
            runtime.stop()

    def test_runtime_never_returns_an_unvalidated_move_from_fake(self):
        board = chess.Board()
        bad = chess.Move.from_uci('e2e5')
        self.assertNotIn(bad, board.legal_moves)


    def test_generate_retries_when_model_returns_only_uci_label(self):
        import json
        from unittest.mock import patch
        from engine_runner import LlamafileRuntime
        class Response:
            def __init__(self, content):
                self.body = json.dumps({'choices': [{'message': {'content': content}}]}).encode()
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self): return self.body
        outputs = iter(['UCI', 'g1h3'])
        requests = []
        def fake_urlopen(request, timeout=None):
            requests.append(json.loads(request.data.decode()))
            return Response(next(outputs))
        events = []
        runtime = LlamafileRuntime(on_status=lambda stage, message, **fields: events.append((stage, fields)))
        board = chess.Board()
        with patch.object(runtime, 'ensure_loaded'), patch('urllib.request.urlopen', side_effect=fake_urlopen):
            move, meta = runtime.generate('/tmp/model.gguf', board)
        self.assertEqual(move.uci(), 'g1h3')
        self.assertEqual(len(requests), 2)
        self.assertIn('poprzednia odpowiedź', requests[1]['messages'][0]['content'])
        self.assertIn('retrying', [stage for stage, _ in events])
        self.assertEqual(meta['response'], 'g1h3')

    def test_generate_retries_illegal_move_and_never_accepts_it(self):
        import json
        from unittest.mock import patch
        from engine_runner import LlamafileRuntime
        class Response:
            def __init__(self, content):
                self.body = json.dumps({'choices': [{'message': {'content': content}}]}).encode()
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self): return self.body
        outputs = iter(['e2e5', 'e2e5'])
        requests = []
        def fake_urlopen(request, timeout=None):
            requests.append(json.loads(request.data.decode()))
            return Response(next(outputs))
        runtime = LlamafileRuntime()
        with patch.object(runtime, 'ensure_loaded'), patch('urllib.request.urlopen', side_effect=fake_urlopen):
            with self.assertRaisesRegex(RunnerError, 'po 2 próbach'):
                runtime.generate('/tmp/model.gguf', chess.Board())
        retry_prompt = requests[1]['messages'][0]['content']
        self.assertIn('e2e5', retry_prompt)
        self.assertIn('NIELEGALNY', retry_prompt)
        self.assertIn('LEGAL MOVES:', retry_prompt)



    def test_watchdog_restarts_an_unexpectedly_stopped_owned_process(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from engine_runner import LlamafileRuntime
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); binary = root / 'llamafile'; model = root / 'model.gguf'
            binary.write_text('#!/bin/sh\\n'); binary.chmod(0o700); model.write_bytes(b'model')
            calls = []
            class Process:
                def __init__(self): self.returncode = None
                def poll(self): return self.returncode
                def terminate(self): self.returncode = 0
                def wait(self, timeout=None): return 0
                def kill(self): self.returncode = -9
            first = Process()
            runtime = LlamafileRuntime(binary=binary, popen=lambda args, **kwargs: (calls.append(args) or Process()))
            runtime._process = first
            runtime._model_path = model.resolve()
            runtime._desired_model_path = model.resolve()
            first.returncode = 1
            with patch.object(runtime, '_healthy', return_value=True):
                runtime._monitor_once()
            self.assertEqual(len(calls), 1)
            self.assertIsNot(runtime._process, first)
            self.assertEqual(runtime.loaded_model, str(model.resolve()))
            runtime.stop()
            runtime._monitor_stop.set()

    def test_watchdog_does_not_restart_after_intentional_stop(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from engine_runner import LlamafileRuntime
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); binary = root / 'llamafile'; model = root / 'model.gguf'
            binary.write_text('#!/bin/sh\\n'); binary.chmod(0o700); model.write_bytes(b'model')
            calls = []
            runtime = LlamafileRuntime(binary=binary, popen=lambda args, **kwargs: (calls.append(args) or object()))
            runtime._desired_model_path = model.resolve()
            runtime.stop()
            with patch.object(runtime, 'ensure_loaded') as ensure_loaded:
                runtime._monitor_once()
                ensure_loaded.assert_not_called()
            self.assertEqual(calls, [])
            runtime._monitor_stop.set()


    def test_watchdog_keeps_retrying_after_failed_recovery_attempt(self):
        from pathlib import Path
        from unittest.mock import patch
        from engine_runner import LlamafileRuntime, RunnerError
        runtime = LlamafileRuntime(monitor_interval=30)
        model = Path('/tmp/arena-managed-model.gguf')
        runtime._desired_model_path = model
        def failed_recovery(path, draft_path=None):
            runtime.stop()
            raise RunnerError('startup failed')
        with patch.object(runtime, 'ensure_loaded', side_effect=failed_recovery):
            runtime._monitor_once()
        self.assertEqual(runtime._desired_model_path, model)
        runtime.shutdown()
        self.assertFalse(runtime._monitor_thread.is_alive())

    def test_shutdown_stops_watchdog_thread(self):
        from engine_runner import LlamafileRuntime
        runtime = LlamafileRuntime(monitor_interval=30)
        runtime.shutdown()
        self.assertTrue(runtime._monitor_stop.is_set())
        self.assertFalse(runtime._monitor_thread.is_alive())


    def test_recent_piece_reversal_is_excluded_when_an_alternative_exists(self):
        from engine_runner import LlamafileRuntime
        board = chess.Board('4k3/8/8/8/8/8/8/4K1R1 w - - 0 1')
        history = [{'ply': 1, 'color': 'white', 'uci': 'h1g1', 'piece': 'R'}]
        allowed, blocked = LlamafileRuntime._allowed_moves(board, history)
        self.assertIn('g1h1', blocked)
        self.assertNotIn('g1h1', [move.uci() for move in allowed])
        self.assertTrue(allowed)

    def test_generate_rejects_a_recent_piece_reversal_and_retries(self):
        import json
        from unittest.mock import patch
        from engine_runner import LlamafileRuntime
        class Response:
            def __init__(self, content):
                self.body = json.dumps({'choices': [{'message': {'content': content}}]}).encode()
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self): return self.body
        outputs = iter(['g1h1', 'g1g2'])
        requests = []
        def fake_urlopen(request, timeout=None):
            requests.append(json.loads(request.data.decode()))
            return Response(next(outputs))
        board = chess.Board('4k3/8/8/8/8/8/8/4K1R1 w - - 0 1')
        history = [{'ply': 1, 'color': 'white', 'uci': 'h1g1', 'piece': 'R'}]
        runtime = LlamafileRuntime()
        with patch.object(runtime, 'ensure_loaded'), patch('urllib.request.urlopen', side_effect=fake_urlopen):
            move, _ = runtime.generate('/tmp/model.gguf', board, recent_moves=history)
        self.assertEqual(move.uci(), 'g1g2')
        first_prompt = requests[0]['messages'][0]['content']
        retry_prompt = requests[1]['messages'][0]['content']
        self.assertNotIn('g1h1', first_prompt.split('LEGAL MOVES: ', 1)[1])
        self.assertIn('powtarza cofnięcie', retry_prompt)
        self.assertIn('g1g2', retry_prompt)

    def test_piece_reversal_is_not_blocked_when_piece_is_no_longer_on_destination(self):
        from engine_runner import LlamafileRuntime
        board = chess.Board('4k3/8/8/8/8/6R1/8/4K3 w - - 0 1')
        history = [{'ply': 1, 'color': 'white', 'uci': 'h1g1', 'piece': 'R'}]
        allowed, blocked = LlamafileRuntime._allowed_moves(board, history)
        self.assertNotIn('g1h1', blocked)
        self.assertTrue(allowed)

    def test_anti_oscillation_never_blocks_a_check_evasion(self):
        from engine_runner import LlamafileRuntime
        board = chess.Board('4r1k1/8/8/8/8/8/8/4K3 w - - 0 1')
        self.assertTrue(board.is_check())
        history = [{'ply': 1, 'color': 'white', 'uci': 'd1e1', 'piece': 'K'}]
        allowed, blocked = LlamafileRuntime._allowed_moves(board, history)
        self.assertEqual(blocked, [])
        self.assertIn(chess.Move.from_uci('e1d1'), allowed)

    def test_sequential_runner_passes_move_history_to_both_models(self):
        sequence = ['f2f3', 'e7e5', 'g2g4', 'd8h4']
        class HistoryRuntime:
            def __init__(self): self.histories=[]
            def generate(self, path, board, stop_event=None, recent_moves=None):
                self.histories.append(list(recent_moves or []))
                uci = sequence[len(self.histories)-1]
                return chess.Move.from_uci(uci), {'seconds': 0.01}
        fake = HistoryRuntime()
        SequentialChessRunner(fake).play_game(
            {'name':'Alpha','path':'/tmp/a.gguf'},
            {'name':'Beta','path':'/tmp/b.gguf'})
        self.assertEqual(fake.histories[0], [])
        self.assertEqual(fake.histories[1][0]['uci'], 'f2f3')
        self.assertEqual(fake.histories[1][0]['piece'], 'P')
        self.assertEqual(fake.histories[2][1]['uci'], 'e7e5')
        self.assertEqual(fake.histories[2][1]['piece'], 'p')


if __name__ == '__main__':
    unittest.main(verbosity=2)
