import hashlib
import json
import sys
import unittest
from pathlib import Path

import chess

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from build_smol_chess_seed_dataset import build_rows


class SmolChessDatasetTests(unittest.TestCase):
    def test_seed_dataset_hash_and_manifest(self):
        data_path = ROOT / "datasets" / "chess_sft" / "seed.jsonl"
        manifest = json.loads((data_path.parent / "manifest.json").read_text(encoding="utf-8"))
        raw = data_path.read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), manifest["sha256"])
        self.assertEqual(manifest["license"], "CC0-1.0")
        self.assertEqual(manifest["external_sources"], [])
        self.assertEqual(manifest["rows"], 8)

    def test_rows_are_deterministic_legal_and_have_unique_mate(self):
        data_path = ROOT / "datasets" / "chess_sft" / "seed.jsonl"
        rows = [json.loads(line) for line in data_path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(rows, build_rows())
        self.assertEqual(len({row["fen"] for row in rows}), len(rows))
        self.assertEqual({row["split"] for row in rows}, {"train", "validation", "test"})
        for row in rows:
            board = chess.Board(row["fen"])
            legal = sorted(move.uci() for move in board.legal_moves)
            self.assertEqual(row["legal_moves"], legal)
            self.assertIn(row["best_move"], legal)
            move = chess.Move.from_uci(row["best_move"])
            board.push(move)
            self.assertTrue(board.is_checkmate())
            self.assertEqual(row["source"], "synthetic_kqk_mate_in_one_v1")
            self.assertEqual(row["license"], "CC0-1.0")


if __name__ == "__main__":
    unittest.main()
