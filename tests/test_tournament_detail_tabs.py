import pathlib
import subprocess
import unittest
from html.parser import HTMLParser

ROOT = pathlib.Path(__file__).resolve().parents[1]

class TournamentDetailTests(unittest.TestCase):
    def test_tab_relationships(self):
        class Parser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.elements = {}
            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if 'id' in attrs:
                    self.elements[attrs['id']] = attrs
        parser = Parser()
        parser.feed((ROOT / 'index.html').read_text())
        for key in ('standings', 'fixtures', 'bracket'):
            tab = parser.elements['tournament-tab-' + key]
            pane = parser.elements['tournament-pane-' + key]
            self.assertEqual(tab['role'], 'tab')
            self.assertEqual(pane['role'], 'tabpanel')
            self.assertEqual(tab['aria-controls'], pane['id'])
            self.assertEqual(pane['aria-labelledby'], tab['id'])
            self.assertEqual('hidden' in pane, key != 'standings')

    def test_standings_before_and_after_saved_result(self):
        js = (ROOT / 'web/js/arena.js').read_text()
        helper = js[js.index('function tournamentStandingRows('):js.index('function renderTournamentRosterCounts(')]
        subprocess.run(['node', '-e', helper + """
const assert=require('node:assert/strict');
const standings=[{name:'A',points:0,played:0,wins:0,draws:0,losses:0}];
let d={progress:{league:{completed:0}},standings};
assert.deepEqual(tournamentStandingRows(d),{ranked:false,headers:['Zawodnik','Pkt','Partie'],rows:[['A','—',0]]});
d.progress.league.completed=1;standings[0].points=1;standings[0].played=1;standings[0].wins=1;
assert.deepEqual(tournamentStandingRows(d).rows,[[1,'A',1,1,1,0,0]]);
"""],check=True)

if __name__ == '__main__':
    unittest.main()
