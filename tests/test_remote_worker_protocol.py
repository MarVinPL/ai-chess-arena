import unittest
import chess
from remote_worker_protocol import ProtocolError, validate_job, validate_result


class RemoteWorkerProtocolTests(unittest.TestCase):
    def setUp(self):
        self.board = chess.Board()
        self.job = {'version': 1, 'job_id': 'game_1234_move_1', 'fen': self.board.fen(),
                    'model_sha256': 'a' * 64, 'max_tokens': 16, 'timeout_seconds': 300}

    def test_valid_job_and_legal_move(self):
        job = validate_job(self.job)
        self.assertEqual(validate_result({'version': 1, 'job_id': job['job_id'], 'uci': 'e2e4'},
                                         job, self.board), chess.Move.from_uci('e2e4'))


    def test_protocol_v2_requires_complete_canonical_arena_legal_moves(self):
        board = self.board.copy()
        legal = sorted(move.uci() for move in board.legal_moves)
        v2 = {**self.job, 'version': 2, 'legal_moves': legal}
        self.assertEqual(validate_job(v2)['legal_moves'], legal)
        self.assertEqual(
            validate_result(
                {'version': 1, 'job_id': v2['job_id'], 'uci': 'e2e4'},
                v2, board,
            ), chess.Move.from_uci('e2e4'),
        )
        bad_lists = [
            [], legal[:-1], list(reversed(legal)),
            legal + [legal[0]], legal + ['e7e5'],
            'e2e4', ['e2e4', 'some extra code'],
        ]
        for wrong in bad_lists:
            with self.subTest(wrong=wrong), self.assertRaises(ProtocolError):
                validate_job({**v2, 'legal_moves': wrong})
        with self.assertRaises(ProtocolError):
            validate_job({**self.job, 'legal_moves': legal})  # v1 forbids extras
        with self.assertRaises(ProtocolError):
            validate_job({**v2, 'unknown': 'not allowed'})

    def test_v3_elimination_keeps_legal_king_capture_and_other_moves(self):
        from elimination_chess import EliminationBoard
        board = EliminationBoard('4k3/8/8/8/8/8/4R3/4K3 w - - 0 1')
        job = {**self.job, 'version': 3, 'variant': 'elimination', 'fen': board.fen(),
               'legal_moves': sorted(m.uci() for m in board.legal_moves)}
        self.assertEqual(validate_job(job), job)
        for uci in ('e2e8', 'e2e7'):
            self.assertEqual(validate_result({'version': 1, 'job_id': job['job_id'], 'uci': uci},
                                             job, board).uci(), uci)
        for altered in ({**job, 'variant': 'unknown'}, {**job, 'variant': 'standard'},
                        {**job, 'legal_moves': ['e2e8']}, {**job, 'version': 2}):
            with self.assertRaises(ProtocolError):
                validate_job(altered)

    def test_rejects_unexpected_command_and_paths(self):
        for extra in ({'command': 'rm -rf /'}, {'model_path': '/home/user/private'}):
            with self.subTest(extra=extra), self.assertRaises(ProtocolError):
                validate_job({**self.job, **extra})

    def test_rejects_invalid_budgets_and_bool(self):
        for key, value in [('timeout_seconds', 601), ('timeout_seconds', True),
                           ('max_tokens', 0), ('max_tokens', 9999), ('version', True)]:
            with self.subTest(key=key, value=value), self.assertRaises(ProtocolError):
                validate_job({**self.job, key: value})

    def test_rejects_wrong_job_or_illegal_move(self):
        for result in ({'version': 1, 'job_id': 'wrong_job_id', 'uci': 'e2e4'},
                       {'version': 1, 'job_id': self.job['job_id'], 'uci': 'e2e5'},
                       {'version': 1, 'job_id': self.job['job_id'], 'uci': 'e7e5'}):
            with self.subTest(result=result), self.assertRaises(ProtocolError):
                validate_result(result, self.job, self.board)

    def test_rejects_bad_digest_and_fen(self):
        for key, value in [('model_sha256', 'abc'), ('fen', 'not a fen')]:
            with self.subTest(key=key), self.assertRaises(ProtocolError):
                validate_job({**self.job, key: value})


if __name__ == '__main__':
    unittest.main()
