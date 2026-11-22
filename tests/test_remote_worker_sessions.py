import unittest
import chess
from remote_worker_protocol import ProtocolError
from remote_worker_sessions import WorkerSessions


class WorkerSessionTests(unittest.TestCase):
    def setUp(self):
        self.now = [100.0]
        self.s = WorkerSessions(clock=lambda: self.now[0])
        self.worker = 'friend_linux_01'
        self.code = self.s.request_pairing(self.worker)
        self.token = self.s.approve(self.worker, self.code)
        self.board = chess.Board()
        self.job = dict(version=1, job_id='game_1234_ply_1', fen=self.board.fen(),
                        model_sha256='b' * 64, max_tokens=16, timeout_seconds=90)
        self.result = dict(version=1, job_id=self.job['job_id'], uci='e2e4')

    def test_pairing_code_one_time_and_secret_not_stored(self):
        with self.assertRaises(ProtocolError):
            self.s.approve(self.worker, self.code)
        self.assertNotIn(self.token, repr(self.s._sessions))

    def test_authorized_move_only_once(self):
        self.s.assign(self.worker, self.token, self.job)
        self.assertEqual(self.s.accept(self.worker, self.token, self.result, self.board).uci(), 'e2e4')
        with self.assertRaises(ProtocolError):
            self.s.accept(self.worker, self.token, self.result, self.board)

    def test_revocation_invalidates_active_job(self):
        self.s.assign(self.worker, self.token, self.job)
        self.s.revoke(self.worker)
        with self.assertRaises(ProtocolError):
            self.s.accept(self.worker, self.token, self.result, self.board)

    def test_expired_job_and_session(self):
        self.s.assign(self.worker, self.token, self.job)
        self.now[0] += 91
        with self.assertRaises(ProtocolError):
            self.s.accept(self.worker, self.token, self.result, self.board)
        self.now[0] += 3600
        with self.assertRaises(ProtocolError):
            self.s.authenticate(self.worker, self.token)

    def test_forged_result_and_changed_board(self):
        self.s.assign(self.worker, self.token, self.job)
        with self.assertRaises(ProtocolError):
            self.s.accept(self.worker, 'fake', self.result, self.board)
        self.board.push_uci('e2e4')
        with self.assertRaises(ProtocolError):
            self.s.accept(self.worker, self.token, self.result, self.board)

    def test_wrong_pairing_code_and_expired_pairing(self):
        other = self.s.request_pairing('friend_macos_02')
        with self.assertRaises(ProtocolError):
            self.s.approve('friend_macos_02', 'wrong')
        self.now[0] += 301
        with self.assertRaises(ProtocolError):
            self.s.approve('friend_macos_02', other)

    def test_duplicate_job(self):
        self.s.assign(self.worker, self.token, self.job)
        with self.assertRaises(ProtocolError):
            self.s.assign(self.worker, self.token, self.job)


if __name__ == '__main__':
    unittest.main()

class WorkerMetricsIntegrationTests(unittest.TestCase):
    def test_coordinator_commits_once_and_keeps_history_after_revoke(self):
        import tempfile
        from pathlib import Path
        from remote_worker_metrics import WorkerMetrics
        with tempfile.TemporaryDirectory() as directory:
            metrics = WorkerMetrics(Path(directory) / 'metrics.db')
            now = [100.0]
            sessions = WorkerSessions(clock=lambda: now[0], metrics=metrics)
            worker = 'friend_linux_01'
            token = sessions.approve(worker, sessions.request_pairing(worker))
            board = chess.Board()
            job = dict(version=1, job_id='game_1234_ply_1', fen=board.fen(),
                       model_sha256='b' * 64, max_tokens=16, timeout_seconds=90)
            sessions.assign(worker, token, job)
            now[0] += 2
            sessions.accept(worker, token, dict(version=1, job_id=job['job_id'], uci='e2e4'), board)
            with self.assertRaises(ProtocolError):
                sessions.accept(worker, token, dict(version=1, job_id=job['job_id'], uci='e2e4'), board)
            job['job_id'] = 'game_1234_ply_2'
            sessions.assign(worker, token, job)
            now[0] += 3
            sessions.terminate(worker, token, job['job_id'], status='error')
            sessions.revoke(worker)
            result = WorkerMetrics(Path(directory) / 'metrics.db').summary()[0]
            self.assertEqual((result['tasks'], result['successes'], result['errors'], result['moves']), (2, 1, 1, 1))
            self.assertEqual(result['duration_ms'], 5000)
