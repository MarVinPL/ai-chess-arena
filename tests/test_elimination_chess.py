import io
import chess
import chess.pgn
import unittest
from elimination_chess import EliminationBoard


class EliminationBoardTests(unittest.TestCase):
    def test_winning_king_capture_moves_are_exposed(self):
        board = EliminationBoard('4k3/8/8/8/8/8/4R3/4K3 w - - 0 1')
        self.assertEqual([move.uci() for move in board.winning_king_capture_moves()], ['e2e8'])

    def test_opposing_king_can_be_captured_as_a_legal_move(self):
        board = EliminationBoard('4k3/8/8/8/8/8/4R3/4K3 w - - 0 1')
        move = chess.Move.from_uci('e2e8')
        self.assertIn(move, board.legal_moves)
        self.assertEqual(board.san(move), 'Rxe8')
        board.push(move)
        self.assertIsNone(board.king(chess.BLACK))
        self.assertEqual(board.outcome().winner, chess.WHITE)
        self.assertEqual(board.result(), '1-0')
        self.assertTrue(board.is_game_over())

    def test_king_capture_is_legal_even_if_own_king_remains_in_check(self):
        board = EliminationBoard('4k3/8/8/8/8/8/4R3/4K2r w - - 0 1')
        move = chess.Move.from_uci('e2e8')
        self.assertIn(move, board.legal_moves)
        board.push(move)
        self.assertEqual(board.result(), '1-0')

    def test_checkmate_does_not_end_the_game_and_piece_moves_remain_available(self):
        board = EliminationBoard()
        for uci in ('f2f3', 'e7e5', 'g2g4', 'd8h4'):
            board.push_uci(uci)
        self.assertTrue(board.is_check())
        self.assertFalse(board.is_checkmate())
        self.assertGreater(board.legal_moves.count(), 0)
        self.assertIsNone(board.outcome())
        self.assertFalse(board.is_game_over())

    def test_kings_alone_are_not_insufficient_material_draw(self):
        board = EliminationBoard('4k3/8/8/8/8/8/8/4K3 w - - 0 1')
        self.assertTrue(board.is_insufficient_material())
        self.assertIsNone(board.outcome())
        self.assertFalse(board.is_game_over())
        self.assertGreater(board.legal_moves.count(), 0)

    def test_black_can_capture_the_white_king_and_win(self):
        board = EliminationBoard('4k3/4r3/8/8/8/8/8/4K3 b - - 0 1')
        move = chess.Move.from_uci('e7e1')
        self.assertIn(move, board.legal_moves)
        board.push(move)
        self.assertIsNone(board.king(chess.WHITE))
        self.assertEqual(board.result(), '0-1')

    def test_position_before_king_capture_is_not_terminal(self):
        board = EliminationBoard()
        self.assertIsNone(board.outcome())
        self.assertFalse(board.is_game_over())

    def test_checkpoint_fen_without_opposing_king_restores_terminal_state(self):
        board = EliminationBoard('4R3/8/8/8/8/8/8/4K3 b - - 0 1')
        self.assertIsNone(board.king(chess.BLACK))
        self.assertTrue(board.is_game_over())
        self.assertEqual(board.result(), '1-0')

    def test_pgn_round_trip_preserves_elimination_variant_and_king_capture(self):
        board = EliminationBoard('4k3/8/8/8/8/8/4R3/4K3 w - - 0 1')
        board.push_uci('e2e8')
        game = chess.pgn.Game.from_board(board)
        serialized = str(game)
        self.assertIn('[Variant "Elimination Chess"]', serialized)
        restored = chess.pgn.read_game(io.StringIO(serialized))
        self.assertEqual(restored.errors, [])
        self.assertIsInstance(restored.board(), EliminationBoard)
        self.assertEqual(restored.end().board().result(), '1-0')


if __name__ == '__main__':
    unittest.main(verbosity=2)
