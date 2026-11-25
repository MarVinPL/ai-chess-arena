import unittest
from unittest.mock import patch
import server


class ExecutionProgressTests(unittest.TestCase):
    def setUp(self):
        self.status = {'stage': 'idle', 'events': []}
        self.patch = patch.object(server, 'live_status', self.status)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        log = patch.object(server, '_append_jsonl')
        log.start()
        self.addCleanup(log.stop)

    def test_same_stage_keeps_start_time(self):
        with patch.object(server.time, 'time', side_effect=[10, 20, 30]):
            server.set_live('model_loading', 'loading', model='A')
            server.set_live('model_loading', 'still loading', model='A')
            self.assertEqual(self.status['stage_started_at'], 10)
            self.assertEqual(self.status['updated_at'], 20)
            server.set_live('model_ready', 'ready', model='A')
            self.assertEqual(self.status['stage_started_at'], 30)

    def test_new_model_resets_stage_clock(self):
        with patch.object(server.time, 'time', side_effect=[10, 20]):
            server.set_live('model_loading', 'loading', model='A')
            server.set_live('model_loading', 'loading', model='B')
        self.assertEqual(self.status['stage_started_at'], 20)

    def test_control_test_clears_previous_pair(self):
        server.set_live('game_running', 'running', pairing_id=9, white='A',
                        black='B', moves=[{'uci': 'e2e4'}], question_no=3)
        server.set_live('passkey_prepare', 'test', model='C',
                        execution_phase='model_test')
        self.assertIsNone(self.status.get('pairing_id'))
        self.assertIsNone(self.status.get('question_no'))
        self.assertEqual(self.status['moves'], [])
        self.assertEqual(self.status['execution_phase'], 'model_test')

    def test_new_pair_clears_previous_error(self):
        server.set_live('error', 'failed', error='old', model='A')
        server.set_live('game_prepare', 'prepare', pairing_id=10,
                        execution_phase='game', moves=[])
        self.assertIsNone(self.status.get('error'))
        self.assertIsNone(self.status.get('model'))
        self.assertEqual(self.status['pairing_id'], 10)



class DownloadProgressTests(unittest.TestCase):
    def test_rate_limit_and_final_update(self):
        from model_store import _progress_reporter
        received = []
        with patch('model_store.time.monotonic', side_effect=[0, .1, 1.1, 1.2]):
            report = _progress_reporter(lambda n, t: received.append((n, t)), 100)
            report(0, force=True)
            report(5)
            report(50)
            report(100, force=True)
        self.assertEqual(received, [(0, 100), (50, 100), (100, 100)])

    def test_progress_callback_failure_does_not_abort_download(self):
        from model_store import _progress_reporter
        def fail(*args):
            raise RuntimeError('telemetry failure')
        _progress_reporter(fail, 100)(0, force=True)

    def test_download_fields_cleared(self):
        with patch.object(server, 'live_status', {'stage':'idle','events':[]}), patch.object(server, '_append_jsonl'):
            server.set_live('model_downloading', 'download', download_received_bytes=4, download_total_bytes=10, asset_kind='draft')
            server.set_live('model_verifying', 'verify', asset_kind='draft')
            self.assertNotIn('download_received_bytes', server.live_status)
            server.set_live('model_loading', 'loading')
            self.assertNotIn('asset_kind', server.live_status)
