"""Regression checks for locally bundled panel icons (Issue #348)."""
import json
import pathlib
import re
import shutil
import subprocess
import unittest
import web_panel_backend
from html.parser import HTMLParser

ROOT = pathlib.Path(__file__).resolve().parents[1]
HTML = (ROOT / "index.html").read_text(encoding="utf-8")
JS = (ROOT / "web/js/panel-icons.js").read_text(encoding="utf-8")
SERVER = (ROOT / "server.py").read_text(encoding="utf-8")


class IconMarkup(HTMLParser):
    def __init__(self):
        super().__init__()
        self.icons = []
        self.unmarked = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "data-icon" in attrs:
            self.icons.append(attrs["data-icon"])
        elif tag in {"button", "a", "h2", "h3", "h4", "h5", "summary"}:
            self.unmarked.append((tag, attrs.get("id")))


class PanelIconTests(unittest.TestCase):
    def test_static_markup_uses_known_icons(self):
        shapes = json.loads(re.search(r"const shapes = Object.freeze\((.*)\);", JS).group(1))
        parser = IconMarkup()
        parser.feed(HTML)
        self.assertTrue(parser.icons)
        self.assertEqual(parser.unmarked, [])
        self.assertEqual(set(parser.icons) - shapes.keys(), set())
        for path in ("web/js/arena.js", "web/js/navigation.js"):
            source = (ROOT / path).read_text(encoding="utf-8")
            names = re.findall(r'\.dataset\.icon\s*=\s*["\']([\w-]+)["\']', source)
            self.assertEqual(set(names) - shapes.keys(), set())

    def test_asset_and_load_order(self):
        self.assertEqual(web_panel_backend.ASSETS["/assets/panel-icons.js"], ("web/js/panel-icons.js", "text/javascript; charset=utf-8"))
        self.assertIn("web_panel_backend.serve_panel_asset(self, url_path, ROOT)", SERVER)
        self.assertLess(HTML.index("/assets/panel-icons.js"), HTML.index("/assets/navigation.js"))
        self.assertLess(HTML.index("/assets/panel-icons.js"), HTML.index("/assets/arena.js"))

    def test_renderer_behavior(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js unavailable")
        script = r"""
const assert = require('assert'), fs = require('fs'), vm = require('vm');
class El {
  constructor(tag) { this.tag = tag; this.children = []; this.attrs = {}; }
  setAttribute(k,v) { this.attrs[k] = v; }
  getAttribute(k) { return this.attrs[k]; }
  hasAttribute(k) { return k in this.attrs; }
  appendChild(e) { e.parent = this; this.children.push(e); }
  prepend(e) { e.parent = this; this.children.unshift(e); }
  remove() { this.parent.children = this.parent.children.filter(e => e !== this); }
}
const window = {};
const document = {createElementNS: (_,tag) => new El(tag), addEventListener() {}};
vm.runInNewContext(fs.readFileSync('web/js/panel-icons.js','utf8'), {window,document});
const api = window.arenaIcons, button = new El('button'), label = new El('span');
button.appendChild(label);
assert(api.setIcon(button,'play'));
const first = button.children[0];
assert.strictEqual(first.getAttribute('aria-hidden'),'true');
assert.strictEqual(first.getAttribute('focusable'),'false');
assert(api.setIcon(button,'play'));
assert.strictEqual(button.children[0], first);
assert(api.setIcon(button,'pause'));
assert.strictEqual(button.children.length,2);
assert.strictEqual(button.children[0].getAttribute('data-arena-icon'),'pause');
assert.strictEqual(button.children[1],label);
assert.strictEqual(api.createIcon('unknown-icon'),null);
"""
        subprocess.run([node, "-e", script], cwd=ROOT, check=True)
