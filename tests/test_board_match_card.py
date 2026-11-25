"""Issue #300: FEN, move arrows, captured-material balance and contrast tests."""
import pathlib
import re
import shutil
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
HTML = (ROOT / "index.html").read_text(encoding="utf-8")
JS = (ROOT / "web/js/arena.js").read_text(encoding="utf-8")
CSS = (ROOT / "web/css/board-redesign.css").read_text(encoding="utf-8")


class BoardMatchCardTests(unittest.TestCase):
    def run_node(self, script):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js not available")
        result = subprocess.run([node, "-e", script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_opposing_cards_and_one_instance_per_control(self):
        board = HTML.index('id="board" class="board"')
        black = HTML.index('id="black" class="player-badge')
        white = HTML.index('id="white" class="player-badge')
        self.assertLess(black, board)
        self.assertLess(board, white)
        for name in ("board", "white", "black", "last-move-note", "white-piece-types",
                     "black-piece-types", "white-captured", "black-captured",
                     "step", "run", "stop", "reset"):
            self.assertEqual(HTML.count('id="' + name + '"'), 1, name)
        self.assertIn('href="/assets/board-redesign.css"', HTML)

    def test_color_is_derived_from_piece_identity_not_square_color(self):
        self.assertIn('data-piece-color="${p?(p===p.toUpperCase()?', JS)
        self.assertIn('.sq[data-piece-color="white"] .piece', CSS)
        self.assertIn('.sq[data-piece-color="black"] .piece', CSS)
        self.assertIn('.board-arrows{pointer-events:none', CSS)

    def test_rendered_material_and_last_move(self):
        source = JS[JS.index("function pieceInventory(fen,color)"):
                    JS.index("function formatMaterialScore(score)")]
        self.run_node(r"""
const START_FEN='8/8/8/8/8/8/8/8 w - - 0 1';
class Element {
 constructor(){this.children=[];this.dataset={};this.classList={add(){}};this.textContent='';}
 replaceChildren(){this.children=[];this.textContent='';}
 appendChild(child){this.children.push(child);}
 setAttribute(){}
}
const document={createElement:()=>new Element(),createTextNode:text=>({textContent:text})};
const nodes={};
const $=id=>id==='zero-piece-mode'||id==='zero-piece-preview'?null:(nodes[id]||(nodes[id]=new Element()));
""" + source + r"""
const start='rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1';
renderPieceInventory(start);
if(!nodes['white-captured'].textContent.endsWith('0'))throw Error('start captured not zero');
if(!nodes['black-captured'].textContent.endsWith('0'))throw Error('start captured not zero');
const taken='rnbqkbnr/pppp1ppp/8/4P3/8/8/PPPP1PPP/RNBQKBNR b KQkq - 0 2';
renderPieceInventory(taken);
if(!nodes['white-captured'].textContent.endsWith('1'))throw Error('capture not reflected');
if(!nodes['black-captured'].textContent.endsWith('0'))throw Error('black capture wrong');
if(pieceInventory('4k3/8/8/8/8/8/8/3QK3 w - - 0 1','white').material!==9)throw Error('promotion wrong');
renderLastMove([{uci:'e2e4',san:'e4'}]);
if(!nodes['last-move-note'].textContent.includes('e4 · e2e4'))throw Error('last move missing');
renderLastMove([]);
if(nodes['last-move-note'].textContent!=='Ostatni ruch: brak')throw Error('empty move wrong');
""")

    def test_svg_arrows_and_square_colors_for_both_sides(self):
        src = JS[JS.index("function draw(fen,moves=[])"):
                 JS.index("async function post(path)")]
        self.run_node(r"""
const pieces={K:'♔',Q:'♕',R:'♖',B:'♗',N:'♘',P:'♙',k:'♚',q:'♛',r:'♜',b:'♝',n:'♞',p:'♟'};
const nodes={board:{innerHTML:''}};
const $=id=>nodes[id];
""" + src + r"""
draw('4k3/8/8/8/8/8/4P3/3QK3 w - - 0 1',[
 {uci:'e2e4',color:'white'}, {uci:'e7e5',color:'black'}
]);
const markup=nodes.board.innerHTML;
if(!markup.includes('data-piece-color="black"'))throw Error('missing black');
if(!markup.includes('data-piece-color="white"'))throw Error('missing white');
if(!markup.includes('id="arrow-white"')||!markup.includes('id="arrow-black"'))throw Error('arrows missing');
if(!markup.includes('pointer-events:none')&&!markup.includes('class="board-arrows"'))throw Error('overlay missing');
if(!markup.includes('marker-end="url(#arrow-white)"'))throw Error('white arrow missing');
if(!markup.includes('marker-end="url(#arrow-black)"'))throw Error('black arrow missing');
""")

    def test_layout_has_square_board_and_desktop_height_constraint(self):
        self.assertIn("aspect-ratio:1/1", CSS)
        self.assertIn("100dvh - 445px", CSS)
        self.assertIn("@media(orientation:portrait),(max-width:760px)", CSS)
        self.assertIn("@media(max-width:390px)", CSS)
        self.assertIn("button:focus-visible", CSS)


if __name__ == "__main__":
    unittest.main()
