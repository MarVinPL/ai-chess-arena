import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "index.html").read_text(encoding="utf-8")


def inventory(fen, color):
    node = shutil.which("node")
    if not node:
        raise unittest.SkipTest("Node.js is required to exercise the browser inventory function")
    match = re.search(r"function pieceInventory\(fen,color\)\{.*?\n", HTML)
    if not match:
        raise AssertionError("pieceInventory function is missing")
    source = match.group(0).strip()
    script = source + f"\nprocess.stdout.write(JSON.stringify(pieceInventory({json.dumps(fen)},{json.dumps(color)})));"
    result = subprocess.run([node, "-e", script], check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


class PieceInventoryTests(unittest.TestCase):
    START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"

    def test_starting_position_counts_all_pieces_and_material(self):
        for color in ("white", "black"):
            with self.subTest(color=color):
                result = inventory(self.START, color)
                self.assertEqual(result["counts"], {"k": 1, "q": 1, "r": 2, "b": 2, "n": 2, "p": 8})
                self.assertEqual(result["total"], 16)
                self.assertEqual(result["material"], 39)

    def test_capture_reduces_only_the_captured_side_inventory(self):
        fen = "rnbqkbnr/pppp1ppp/8/4P3/8/8/PPPP1PPP/RNBQKBNR b KQkq - 0 2"
        white = inventory(fen, "white")
        black = inventory(fen, "black")
        self.assertEqual(white["total"], 16)
        self.assertEqual(black["counts"]["p"], 7)
        self.assertEqual(black["total"], 15)
        self.assertEqual(black["material"], 38)

    def test_promoted_pawn_is_counted_as_its_current_piece_type(self):
        fen = "4k3/8/8/8/8/8/8/3QK3 w - - 0 1"
        white = inventory(fen, "white")
        self.assertEqual(white["counts"]["p"], 0)
        self.assertEqual(white["counts"]["q"], 1)
        self.assertEqual(white["total"], 2)
        self.assertEqual(white["material"], 9)

    def test_black_and_white_piece_case_is_not_mixed(self):
        fen = "4k3/8/8/8/8/8/4p3/3QK3 w - - 0 1"
        self.assertEqual(inventory(fen, "white")["counts"]["q"], 1)
        self.assertEqual(inventory(fen, "white")["counts"]["p"], 0)
        self.assertEqual(inventory(fen, "black")["counts"]["p"], 1)
        self.assertEqual(inventory(fen, "black")["counts"]["q"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
