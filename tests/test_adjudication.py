import random
import tempfile
import unittest
from pathlib import Path

import chess
import chess.engine

import adjudication


def position_after_40_plies():
    for seed in range(20):
        rng = random.Random(seed)
        board = chess.Board()
        records = []
        for _ in range(40):
            move = rng.choice(list(board.legal_moves))
            board.push(move)
            records.append({"uci": move.uci()})
            if board.is_game_over(claim_draw=True):
                break
        if len(records) == 40 and not board.is_game_over(claim_draw=True):
            return board, records
    raise AssertionError("Could not construct a non-terminal 40-ply position")


class FakeEngine:
    id = {"name": "Fake Stockfish"}
    def __init__(self, scores=None, moves=None):
        self.scores = scores or [-1200, -1100]
        self.moves = moves
        self.calls = 0
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def configure(self, options): pass
    def analyse(self, board, limit):
        idx = self.calls
        self.calls += 1
        move = (self.moves[idx] if self.moves else next(iter(board.legal_moves)))
        return {"score": chess.engine.PovScore(chess.engine.Cp(self.scores[idx]), chess.WHITE),
                "pv": [move]}


class AdjudicationTests(unittest.TestCase):
    def setUp(self):
        self.board, self.records = position_after_40_plies()

    def test_no_configured_engine_fails_closed(self):
        result = adjudication.adjudicate_failure(
            self.board.fen(), self.records, "white", engine_path="")
        self.assertEqual(result["status"], "unresolved")
        self.assertEqual(result["reason"], "analysis_engine_not_configured")

    def test_short_game_is_not_adjudicated(self):
        result = adjudication.adjudicate_failure(
            self.board.fen(), self.records[:-1], "white", engine_path="/bin/true")
        self.assertEqual(result["reason"], "insufficient_game_length")

    def test_illegal_move_attempt_is_never_adjudicated(self):
        result = adjudication.adjudicate_failure(
            self.board.fen(), self.records, "white",
            uci_attempts=[{"classification": "illegal_uci"}],
            engine_path="/bin/true")
        self.assertEqual(result["reason"], "illegal_or_forbidden_move_attempt")

    def test_fen_must_match_replayed_legal_history(self):
        result = adjudication.adjudicate_failure(
            chess.Board().fen(), self.records, "white", engine_path="/bin/true")
        self.assertEqual(result["reason"], "fen_history_mismatch")

    def test_invalid_fen_is_unresolved(self):
        result = adjudication.adjudicate_failure(
            "not a fen", self.records, "white", engine_path="/bin/true")
        self.assertEqual(result["reason"], "invalid_fen_or_move_history")

    def test_stable_decisive_local_analysis_adjudicates_for_opponent(self):
        engine = FakeEngine()
        result = adjudication.adjudicate_failure(
            self.board.fen(), self.records, "white", engine_path="/bin/true",
            engine_factory=lambda *args, **kwargs: engine)
        self.assertEqual(result["status"], "adjudicated")
        self.assertEqual(result["winner_color"], "black")
        self.assertEqual(result["engine"], "Fake Stockfish")
        self.assertEqual([x["depth"] for x in result["analyses"]], [12, 16])
        self.assertTrue(all(x["score_cp"] >= 800 for x in result["analyses"]))

    def test_advantage_below_threshold_remains_unresolved(self):
        engine = FakeEngine(scores=[700, 900])
        result = adjudication.adjudicate_failure(
            self.board.fen(), self.records, "white", engine_path="/bin/true",
            engine_factory=lambda *args, **kwargs: engine)
        self.assertEqual(result["reason"], "advantage_below_threshold")

    def test_unstable_best_move_remains_unresolved(self):
        legal = list(self.board.legal_moves)
        engine = FakeEngine(moves=[legal[0], legal[-1]])
        result = adjudication.adjudicate_failure(
            self.board.fen(), self.records, "white", engine_path="/bin/true",
            engine_factory=lambda *args, **kwargs: engine)
        self.assertEqual(result["reason"], "unstable_best_move")

    def test_wrong_failed_side_is_unresolved(self):
        result = adjudication.adjudicate_failure(
            self.board.fen(), self.records, "black", engine_path="/bin/true")
        self.assertEqual(result["reason"], "failed_side_does_not_match_side_to_move")


if __name__ == "__main__":
    unittest.main(verbosity=2)
