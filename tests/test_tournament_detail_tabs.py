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

    def test_fixture_status_result_and_pgn(self):
        js = (ROOT / 'web/js/arena.js').read_text()
        helper = js[js.index('function tournamentFixturePresentation('):js.index('function renderTournamentFixtures(')]
        subprocess.run(['node', '-e', helper + """
const assert=require('node:assert/strict');
const base={stage:'league',leg:1,saved_game_id:42,result:'1/2-1/2',game_url:'/api/games/42/pgn'};
for(const [status,label] of [['pending','Zaplanowana'],['planned','Zaplanowana'],['running','Trwa'],['failed','Nieudana']]){
 const x=tournamentFixturePresentation({...base,status});
 assert.equal(x.status,label);assert.equal(x.result,'—');assert.equal(x.pgn,null);
}
let x=tournamentFixturePresentation({...base,status:'completed'});
assert.equal(x.status,'Zakończona');assert.equal(x.result,'1/2-1/2');assert.equal(x.pgn,base.game_url);
x=tournamentFixturePresentation({...base,status:'completed',saved_game_id:null});
assert.equal(x.status,'Nieudana');assert.equal(x.result,'—');
for(const game_url of [null,'https://example.com','javascript:alert(1)','/api/games/43/pgn']){
 x=tournamentFixturePresentation({...base,status:'completed',game_url});
 assert.equal(x.pgn,null);assert.equal(x.result,'1/2-1/2');
}
x=tournamentFixturePresentation({...base,status:'completed',stage:'draft',round_no:0,adjudication_status:'adjudicated'});
assert.equal(x.group,'Puchar · runda 0');assert.equal(x.adjudicated,true);
"""],check=True)

    def test_bracket_winner_and_champion_require_saved_evidence(self):
        js = (ROOT / 'web/js/arena.js').read_text()
        helper = js[js.index('function tournamentFixturePresentation('):js.index('function renderTournamentFixtures(')]
        helper += js[js.index('function tournamentBracketMatch('):js.index('function renderTournamentBracket(')]
        subprocess.run(['node', '-e', helper + """
const assert=require('node:assert/strict');
const m={status:'completed',saved_game_id:42,result:'1/2-1/2',white_model_id:1,black_model_id:2,white_name:'A',black_name:'B',winner_model_id:2};
assert.equal(tournamentBracketMatch(m).winner,'B');
for(const changes of [{saved_game_id:null},{status:'failed'},{winner_model_id:99},{winner_model_id:null}])
 assert.equal(tournamentBracketMatch({...m,...changes}).winner,null);
const d={tournament:{status:'completed'},champion:{winner_model_id:2},bracket:[{round_no:1,matches:[m]}]};
assert.equal(tournamentChampion(d),'B');
assert.equal(tournamentChampion({...d,tournament:{status:'running'}}),null);
assert.equal(tournamentChampion({...d,bracket:[{round_no:0,matches:[m]}]}),null);
assert.equal(tournamentChampion({...d,bracket:[{round_no:1,matches:[m,m]}]}),null);
assert.equal(tournamentChampion({...d,champion:{winner_model_id:1}}),null);
"""],check=True)

if __name__ == '__main__':
    unittest.main()
