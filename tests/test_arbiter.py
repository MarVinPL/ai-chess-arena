import unittest

import chess
import arbiter


class ArbiterTests(unittest.TestCase):
    def test_repeated_four_ply_cycle_is_detected(self):
        board = chess.Board()
        records = []
        for ply, (color, uci) in enumerate([
            ('white', 'g1f3'), ('black', 'g8f6'), ('white', 'f3g1'), ('black', 'b8c6'),
            ('white', 'g1f3'), ('black', 'c6b8'), ('white', 'f3g1'), ('black', 'f6g8')], 1):
            records.append({'ply': ply, 'color': color, 'uci': uci})
        signal = arbiter.detect_stagnation(board, records)
        self.assertIsNotNone(signal)
        self.assertEqual(signal['type'], 'piece_shuttle')
        self.assertEqual(signal['color'], 'white')

    def test_short_or_non_repeating_history_does_not_trigger(self):
        board = chess.Board()
        records = [{'uci': 'e2e4'}, {'uci': 'e7e5'}]
        self.assertIsNone(arbiter.detect_stagnation(board, records))

    def test_heuristic_signal_never_declares_a_draw(self):
        board = chess.Board()
        records = [{'color': 'white', 'uci': uci} for uci in
                   ['g1f3', 'f3g1', 'g1f3', 'f3g1']]
        signal = arbiter.detect_stagnation(board, records)
        self.assertIsNotNone(signal)
        self.assertFalse(board.is_game_over(claim_draw=True))

    def test_intervention_is_neutral_and_contains_no_move_advice(self):
        prompt = arbiter.intervention_prompt({'type': 'repeated_move_cycle'})
        self.assertIn('oceń pozycję od nowa', prompt.lower())
        self.assertIn('nie otrzymujesz podpowiedzi strategicznej', prompt.lower())
        self.assertNotIn('e2e4', prompt)

    def test_intervention_cap_is_small_and_explicit(self):
        self.assertEqual(arbiter.MAX_INTERVENTIONS_PER_GAME, 3)


if __name__ == '__main__':
    unittest.main()
