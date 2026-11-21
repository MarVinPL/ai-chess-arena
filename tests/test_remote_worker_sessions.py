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
