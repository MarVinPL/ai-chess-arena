import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "index.html").read_text(encoding="utf-8")


class ChessBoardUiTests(unittest.TestCase):
    def test_board_has_arena_pro_player_strip_and_accessible_label(self):
        self.assertIn('class="board-topline"', HTML)
        self.assertIn('class="player-strip"', HTML)
        self.assertIn('id="white" class="player-badge player-badge--white"', HTML)
        self.assertIn('id="black" class="player-badge player-badge--black"', HTML)
        self.assertIn('id="board" class="board" role="img"', HTML)

    def test_coordinates_are_rendered_for_all_board_squares(self):
        self.assertIn('data-file="${String.fromCharCode(97+c)}"', HTML)
        self.assertIn('data-rank="${8-r}"', HTML)
        self.assertIn('.sq[data-file="a"]::before', HTML)
        self.assertIn('.sq[data-rank="1"]::after', HTML)

    def test_analysis_svg_remains_above_board_and_non_interactive(self):
        self.assertIn('class="board-arrows"', HTML)
        self.assertIn('pointer-events:none', HTML)
        self.assertIn('id="arrow-white"', HTML)
        self.assertIn('id="arrow-black"', HTML)
        self.assertIn('marker-end="url(#${marker})"', HTML)

    def test_turquoise_accent_and_responsive_layout_are_defined(self):
        self.assertIn('--arena-accent:#35d2c0', HTML)
        self.assertIn('background:var(--arena-light)', HTML)
        self.assertIn('background:var(--arena-dark)', HTML)
        self.assertRegex(HTML, r'@media\(max-width:760px\)')
        self.assertIn('overflow-x:hidden', HTML)

    def test_material_and_registry_name_rendering_are_unchanged(self):
        self.assertIn('function materialAdvantage(fen)', HTML)
        self.assertIn('function formatMaterialScore(score)', HTML)
        self.assertIn('s.white', HTML)
        self.assertIn('s.black', HTML)
        self.assertIn('formatMaterialScore(material.white)', HTML)
        self.assertIn('formatMaterialScore(material.black)', HTML)

    def test_piece_inventory_is_derived_from_fen_and_counts_material(self):
        self.assertIn('aria-label=\"Pozostałe bierki i materiał\"', HTML)
        self.assertIn('id=\"white-piece-types\"', HTML)
        self.assertIn('id=\"black-piece-types\"', HTML)
        self.assertIn('renderPieceInventory(s.fen||START_FEN)', HTML)
        live_refresh = HTML[HTML.index('async function refreshLive()'):]
        self.assertIn('renderPieceInventory(s.fen||START_FEN)', live_refresh)
        if not shutil.which('node'):
            self.skipTest('Node.js is not installed')
        start = HTML.index('function pieceInventory(fen,color)')
        end = HTML.index('function formatMaterialScore(score)', start)
        functions = HTML[start:end]
        script = functions + "\n" + "const initial=pieceInventory('rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1','white');" + "\n" + "const promoted=pieceInventory('r3k3/8/8/8/8/8/4Q3/4K3 w - - 0 1','white');" + "\n" + "const black=pieceInventory('r3k3/8/8/8/8/8/4Q3/4K3 w - - 0 1','black');" + "\n" + "console.log(JSON.stringify({initial,promoted,black}));"
        result = subprocess.run(['node', '-e', script], check=True, capture_output=True, text=True)
        data = json.loads(result.stdout)
        self.assertEqual(data['initial']['total'], 16)
        self.assertEqual(data['initial']['material'], 39)
        self.assertEqual(data['initial']['counts'], {'k': 1, 'q': 1, 'r': 2, 'b': 2, 'n': 2, 'p': 8})
        self.assertEqual(data['promoted']['counts']['q'], 1)
        self.assertEqual(data['promoted']['material'], 9)
        self.assertEqual(data['promoted']['total'], 2)
        self.assertEqual(data['black']['counts']['r'], 1)
        self.assertEqual(data['black']['material'], 5)
        self.assertEqual(data['black']['total'], 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
