import re
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
