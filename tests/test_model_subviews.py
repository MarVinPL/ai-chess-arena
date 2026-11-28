"""Regression coverage for #302 presentation and read-only navigation."""
import pathlib
import shutil
import subprocess
import unittest
from html.parser import HTMLParser

ROOT = pathlib.Path(__file__).resolve().parents[1]


class ModelSubviewTests(unittest.TestCase):
    def test_existing_controls_are_unique(self):
        class IDs(HTMLParser):
            def __init__(self):
                super().__init__()
                self.ids = []
                self.groups = []

            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if 'id' in attrs:
                    self.ids.append(attrs['id'])
                if 'data-model-group' in attrs:
                    self.groups.append(attrs['data-model-group'])

        parser = IDs()
        parser.feed((ROOT / 'index.html').read_text())
        self.assertEqual(len(parser.ids), len(set(parser.ids)))
        self.assertEqual(set(parser.groups),
                         {'catalog', 'audits', 'downloads', 'cache', 'history'})
        for name in ('model-list', 'model-form', 'model-review-form',
                     'model-admission-list', 'hf-download-workspace',
                     'hf-download-history', 'hf-dash-history', 'managed-models',
                     'registry-query', 'registry-filter'):
            self.assertIn(name, parser.ids)

    def test_mixed_registry_and_download_limits(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node.js unavailable')
        script = r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const s=fs.readFileSync('web/js/arena.js','utf8');
const ctx={};vm.createContext(ctx);
for(const [start,end] of [
 ['function registryPresentation(','let registryCards='],
 ['function downloadSelectionBlockers(','function renderHfDownloadSelection(']
]){
 const a=s.indexOf(start),b=s.indexOf(end,a);
 assert(a>=0&&b>a);
 vm.runInContext(s.slice(a,b),ctx);
}
const model={id:1,filename:'a.gguf',sha256:'a'.repeat(64),size_bytes:2,license_verified:1,managed_copy:1};
const admission=[{model_id:1,status:'eligible',decision:{status:'admitted'},technical_readiness:'verified'}];
let p=ctx.registryPresentation(model,{managed_models:[]},admission);
assert.equal(p.admission,'admitted');
assert.equal(p.local,'Brak potwierdzonej kopii areny');
assert.ok(p.download.includes('zależy od dopuszczenia'));
p=ctx.registryPresentation(model,{managed_models:[{filename:'a.gguf'}]},admission);
assert.equal(p.local,'Kopia lokalna');
p=ctx.registryPresentation({...model,draft_filename:'b.gguf'},{managed_models:[{filename:'a.gguf'}]},admission);
assert.equal(p.local,'Kopia częściowa');assert.equal(p.manifest,false);
p=ctx.registryPresentation(model,null,null);
assert.equal(p.local,'Stan magazynu nieznany');assert.equal(p.admission,'Nie udało się odczytać');
p=ctx.registryPresentation({...model,license_verified:0},{managed_models:[]},[{model_id:1,status:'blocked',blockers:['reason']}]);
assert.equal(p.license,false);assert.equal(p.allowlist,'blocked');assert.equal(p.blockers[0],'reason');
const GiB=1024**3;
assert.equal(ctx.downloadSelectionBlockers([{size_bytes:16*GiB}],true).length,0);
for(const [items,consent] of [
 [[],true],[[{size_bytes:1}],false],[[{size_bytes:16*GiB+1}],true],
 [Array.from({length:4},()=>({size_bytes:1})),true],[[{size_bytes:NaN}],true]
])assert.ok(ctx.downloadSelectionBlockers(items,consent).length);
assert.equal(ctx.downloadStatusLabel('interrupted'),'Przerwane po restarcie');
"""
        result = subprocess.run([node, '-e', script], cwd=ROOT,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_routes_do_not_request_backend(self):
        nav = (ROOT / 'web/js/navigation.js').read_text()
        self.assertNotIn('fetch(', nav)
        self.assertNotIn('cloneNode(', nav)
        self.assertIn('window.arenaRevealTarget', nav)
        self.assertIn("button.setAttribute('aria-controls', pane.id)", nav)
        self.assertIn("button.setAttribute('aria-selected'", nav)
        self.assertIn("pane.hidden = key !== name", nav)
