"""Regression tests for Issue #299's five views without a browser dependency."""
import pathlib
import re
import shutil
import subprocess
import unittest

from web_panel_backend import ASSETS

ROOT = pathlib.Path(__file__).resolve().parents[1]
HTML = (ROOT / "index.html").read_text(encoding="utf-8")
NAV = (ROOT / "web/js/navigation.js").read_text(encoding="utf-8")
SERVER = (ROOT / "server.py").read_text(encoding="utf-8")
PANEL_BACKEND = (ROOT / "web_panel_backend.py").read_text(encoding="utf-8")


class NavigationTests(unittest.TestCase):
    def test_five_visible_routes_and_accessible_links(self):
        for route, label in (
            ("live", "Na żywo"), ("tournaments", "Turnieje"),
            ("models", "Modele"), ("history", "Historia"),
            ("settings", "Ustawienia"),
        ):
            self.assertIn('id="view-' + route + '"', HTML)
            self.assertRegex(HTML, r'href="#view-' + route + r'" data-view-link="' + route + r'"[^>]*>' + re.escape(label))
            self.assertIn('data-arena-view="' + route + '"', HTML)
        self.assertIn('aria-label="Główna nawigacja"', HTML)
        self.assertLess(HTML.index('/assets/navigation.js'), HTML.index('/assets/arena.js'))
        self.assertIn('aria-current', NAV)
        self.assertIn('hashchange', NAV)

    def test_routes_authentication_and_existing_ids(self):
        self.assertIn('/assets/navigation.js', ASSETS)
        self.assertIn('/assets/navigation.css', ASSETS)
        self.assertLess(SERVER.index("if not user:"), SERVER.index("self.send_panel_asset(path)"))
        for unique_id in (
            "board", "games", "tournament-list", "tournament-form",
            "model-list", "model-form", "model-permissions",
            "hf-quarantine-dashboard", "hf-download-planner",
            "managed-models", "league-message",
        ):
            self.assertEqual(HTML.count('id="' + unique_id + '"'), 1)

    def test_navigation_moves_existing_nodes_and_preserves_feedback(self):
        self.assertNotIn('cloneNode(', NAV)
        self.assertNotIn("fetch(", NAV)
        self.assertIn("views.live.append(layout, livePanel)", NAV)
        self.assertIn("views.history.append(historyPanel", NAV)
        self.assertIn("views.tournaments.append(", NAV)
        self.assertIn("views.models.append(", NAV)
        self.assertIn("views.settings.append(", NAV)
        self.assertIn("nav.insertAdjacentElement('afterend', message)", NAV)
        self.assertIn("window.history.pushState", NAV)
        self.assertIn("routeAnchor(anchor)", NAV)

    def test_js_navigation_behavior_with_fake_dom(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js unavailable")
        script = r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert');
class El {
 constructor(id='',dataset={}){this.id=id;this.dataset=dataset;this.children=[];this.parent=null;this.attrs={};this.listeners={};this.hidden=false;}
 append(...nodes){for(const n of nodes){if(n.parent)n.parent.children.splice(n.parent.children.indexOf(n),1);this.children.push(n);n.parent=this;}}
 prepend(...nodes){for(const n of [...nodes].reverse()){if(n.parent)n.parent.children.splice(n.parent.children.indexOf(n),1);this.children.unshift(n);n.parent=this;}}
 remove(){if(this.parent)this.parent.children.splice(this.parent.children.indexOf(this),1);this.parent=null;}
 closest(sel){if(sel==='.layout'&&this.id==='board')return layout;if(sel==='section.panel'&&this.id==='games')return historyPanel;if(sel==='section.panel'&&this.id==='pure-ranking')return league;if(sel==='.arena-view'){let p=this;while(p){if(p.dataset.arenaView)return p;p=p.parent;}return null;}return null;}
 querySelector(sel){if(sel==='.league-grid'&&this===league)return leagueGrid;return null;}
 querySelectorAll(sel){return sel==='[data-view-link]'?links:[];}
 addEventListener(name,fn){this.listeners[name]=fn;}
 setAttribute(name,value){this.attrs[name]=value;}
 removeAttribute(name){delete this.attrs[name];}
 getAttribute(name){return this.attrs[name]||null;}
 contains(node){return node===this||this.children.includes(node)||(this===nav&&links.includes(node));}
 insertAdjacentElement(_where,node){this.inserted=node;node.parent=this;}
 focus(){}
}
const all={};const el=(id,dataset)=>all[id]=new El(id,dataset);
const root=el('arena-view-root'),nav=el('arena-nav');
const views=['live','tournaments','models','history','settings'].map(x=>el('view-'+x,{arenaView:x}));
const links=['live','tournaments','models','history','settings'].map(x=>new El('',{viewLink:x}));
const layout=el('layout'),board=el('board'),games=el('games'),historyPanel=el('historyPanel'),livePanel=el('live-panel');
const league=el('league'),leagueGrid=el('leagueGrid'),pureRank=el('pure-ranking'),engineRank=el('engine-ranking'),modelColumn=el('modelsColumn'),tournamentColumn=el('tournamentColumn');
const permissions=el('model-permissions'),storage=el('storage-info'),message=el('league-message'),hf=el('hf-quarantine-shortcut');
layout.append(board,historyPanel);historyPanel.append(games);league.append(leagueGrid,storage,message);
leagueGrid.append(pureRank,engineRank,modelColumn,tournamentColumn);modelColumn.append(permissions);
const document={getElementById:id=>all[id]||null,createElement:()=>new El(),addEventListener(name,fn){this[name]=fn;},documentElement:{classList:{add(){}}}};
const location={hash:''};
const win={location,history:{pushState(_a,_b,hash){location.hash=hash;}},addEventListener(name,fn){this[name]=fn;}};
vm.runInNewContext(fs.readFileSync('web/js/navigation.js','utf8'),{document,window:win});
assert.strictEqual(root.hidden,false);assert.strictEqual(all['view-live'].hidden,false);
assert.strictEqual(all['view-tournaments'].hidden,true);
assert.strictEqual(layout.parent,all['view-live']);assert.strictEqual(historyPanel.parent,all['view-history']);
assert.strictEqual(modelColumn.parent.children[1],modelColumn);assert.strictEqual(permissions.parent.parent,all['view-settings']);
assert.strictEqual(message.parent,nav);
const link=links[1];nav.listeners.click({target:{closest:()=>link},preventDefault(){}});
assert.strictEqual(location.hash,'#view-tournaments');assert.strictEqual(all['view-tournaments'].hidden,false);
assert.strictEqual(all['view-live'].hidden,true);
location.hash='#view-models';win.hashchange();assert.strictEqual(all['view-models'].hidden,false);
assert.strictEqual(modelColumn.parent,all['view-models'].children[1]);
assert.strictEqual(links[2].attrs['aria-current'],'page');
assert.strictEqual(links[1].attrs['aria-current'],undefined);
assert.strictEqual(leagueGrid.children.length,0);
"""
        result = subprocess.run([node, "-e", script], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
