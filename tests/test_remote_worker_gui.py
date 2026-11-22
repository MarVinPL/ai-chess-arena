from pathlib import Path
import unittest

HTML = Path(__file__).resolve().parents[1] / 'remote_worker_gui' / 'public' / 'index.html'


class WorkerGuiTests(unittest.TestCase):
    def test_gui_exists_with_accessible_controls(self):
        html = HTML.read_text(encoding='utf-8')
        for field in ('server', 'code', 'cpu', 'ram', 'gpu'):
            self.assertIn(f'for="{field}"', html)
            self.assertIn(f'id="{field}"', html)
        for action in ('pair', 'start', 'pause', 'stop', 'disconnect'):
            self.assertIn(f'id="{action}"', html)
            self.assertIn(f'id="{action}"', html.split('<script>')[0])
        self.assertIn('role="status"', html)

    def test_unimplemented_actions_fail_closed(self):
        html = HTML.read_text(encoding='utf-8')
        for action in ('start', 'pause', 'stop', 'disconnect'):
            self.assertRegex(html, rf'id="{action}"[^>]*disabled')
        self.assertIn('id="pair" type="button"', html)
        self.assertIn("invoke('verify_worker_connection'", html)
        self.assertNotIn("invoke('start')", html)
        self.assertNotIn('fetch(', html)
        self.assertNotIn('WebSocket(', html)


if __name__ == '__main__':
    unittest.main()
