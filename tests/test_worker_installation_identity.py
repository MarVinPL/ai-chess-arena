"""Exercise the initial installation identity script without a device or network."""
from pathlib import Path
import shutil
import subprocess
import unittest
from html.parser import HTMLParser

UI = Path(__file__).resolve().parents[1] / 'remote_worker_gui/public/index.html'


class ServerField(HTMLParser):
    value = None
    attrs = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'input' and attrs.get('id') == 'server':
            self.attrs = attrs
            self.value = attrs.get('value')


def test_default_server_is_editable_https():
    parser = ServerField()
    parser.feed(UI.read_text(encoding='utf-8'))
    assert parser.value == 'https://arena.148-113-182-137.sslip.io'
    assert 'readonly' not in parser.attrs and 'disabled' not in parser.attrs


@unittest.skipUnless(shutil.which('node'), 'Node.js required for execution test')
class InstallationIdentityTests(unittest.TestCase):
    def test_initial_identity_lifecycle(self):
        script = r'''
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {webcrypto} = require('node:crypto');
const html = fs.readFileSync(process.argv[1], 'utf8');
const start = html.indexOf('const workerIdentityKey=');
const end = html.indexOf('const cpu=', start);
assert.ok(start >= 0 && end > start);
const source = html.slice(start, end);
const key = 'arena-worker-installation-id-v1';
function launch(storage, fail = false, failCrypto = false) {
  const fields = {workerId: {value: ''}, backendStatus: {textContent: ''}};
  let calls = 0;
  vm.runInNewContext(source, {
    document: {getElementById: name => fields[name]},
    localStorage: {
      getItem: name => storage.get(name) ?? null,
      setItem: (name, value) => {if (fail) throw Error('storage'); storage.set(name, value);}
    },
    crypto: {getRandomValues: bytes => {calls++; if (failCrypto) throw Error('crypto'); return webcrypto.getRandomValues(bytes);}}
  });
  return {id: fields.workerId.value, status: fields.backendStatus.textContent, calls};
}
const store = new Map([['unrelated', 'unchanged']]);
const first = launch(store);
assert.match(first.id, /^android_[0-9a-f]{32}$/);
assert.equal(first.calls, 1);
assert.equal(store.get(key), first.id);
assert.equal(store.get('unrelated'), 'unchanged');
assert.equal(store.size, 2);
const restart = launch(store);
assert.equal(restart.id, first.id);
assert.equal(restart.calls, 0);
const fresh = launch(new Map());
assert.notEqual(fresh.id, first.id);
const corrupt = new Map([[key, 'bad']]);
assert.match(launch(corrupt).id, /^android_[0-9a-f]{32}$/);
for (const failure of [launch(new Map(), true), launch(new Map(), false, true)]) {
  assert.equal(failure.id, '');
  assert.ok(failure.status.includes('Wpisz go ręcznie'));
}
'''
        subprocess.run(['node', '-e', script, str(UI)], check=True, timeout=15)
