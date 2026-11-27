import shutil
import subprocess
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]

class ExecutionProgressRenderingTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node.js unavailable')
    def test_both_cards_render_real_progress_and_clear_stale_data(self):
        script = r"""
const assert = require('assert'), fs = require('fs'), vm = require('vm');
const source = fs.readFileSync('web/js/arena.js', 'utf8');
const start = source.indexOf('function renderExecutionProgress(');
const end = source.indexOf('async function refreshLive()', start);
assert(start >= 0 && end > start);
const selectors = ['stage','context','time','resources','download','bytes'];
const cards = [0,1].map(() => {
  const nodes = Object.fromEntries(selectors.map(s => ['[data-execution-'+s+']',{textContent:'',hidden:false,max:1,value:0}]));
  return {querySelector: s => nodes[s]};
});
const context = {document:{querySelectorAll: () => cards}};
vm.createContext(context);
vm.runInContext(source.slice(start,end),context);
const render = context.renderExecutionProgress;
const verify = fn => cards.forEach(card => fn(s => card.querySelector('[data-execution-'+s+']')));
render({stage:'model_downloading',tournament_id:7,execution_phase:'model_test',model:'Alpha',asset_kind:'draft',stage_started_at:10,updated_at:20,download_received_bytes:50,download_total_bytes:100},30);
verify(n => {
  assert(n('stage').textContent.includes('Pobieranie'));
  assert(n('context').textContent.includes('Test przed rozgrywką'));
  assert(n('context').textContent.includes('Model draft'));
  assert(n('time').textContent.includes('20 s'));
  assert(!n('download').hidden);
  assert.strictEqual(n('download').value,50);
  assert(n('bytes').textContent.includes('50%'));
});
render({stage:'model_verifying',download_received_bytes:50,download_total_bytes:100},30);
verify(n => {assert(n('download').hidden);assert.strictEqual(n('bytes').textContent,'');});
render({stage:'model_downloading',download_received_bytes:101,download_total_bytes:100},30);
verify(n => assert(n('download').hidden));
render({stage:'passkey_generating',execution_phase:'model_test',question_no:0,stop_requested:true},30);
verify(n => {assert(n('stage').textContent.includes('odpowiedź testową'));assert(n('context').textContent.includes('Kontrola losowa'));assert(n('context').textContent.includes('Żądanie zatrzymania'));});
render({stage:'game_running',execution_phase:'game',pairing_id:12,game_state:'active',executor:'local',remote_worker_status:'unavailable',local_resources:{memory_available_mib:100,memory_total_mib:200,cpu_load_1m:1.2,cpu_logical_count:2}},30);
verify(n => {assert(n('context').textContent.includes('Para #12'));assert(n('context').textContent.includes('Partia aktywna'));assert(n('context').textContent.includes('lokalny VPS'));assert(n('context').textContent.includes('Zdalny Worker: brak danych'));assert(n('resources').textContent.includes('100 / 200 MiB'));assert(n('resources').textContent.includes('1.2'));});
render({stage:'game_resume_prepare',game_state:'preparing'},30);
verify(n => {assert(n('context').textContent.includes('Partia w przygotowaniu'));assert(!n('context').textContent.includes('Partia aktywna'));});
render({stage:'stopped',game_state:'paused'},30);
verify(n => assert(n('context').textContent.includes('Wstrzymano')));
render({stage:'error',message:'Awaria',model:'Old'},30);
verify(n => {assert(n('stage').textContent.includes('Awaria'));assert(!n('context').textContent.includes('Old'));});
render({stage:'unavailable'},30);
verify(n => {assert(n('stage').textContent.includes('niedostępny'));assert(n('download').hidden);assert.strictEqual(n('time').textContent,'Brak danych czasowych');});
"""
        subprocess.run(['node', '-e', script], cwd=ROOT, check=True)

    def test_two_cards_and_accessible_progress(self):
        html = (ROOT / 'index.html').read_text()
        self.assertEqual(html.count('class="execution-progress"'), 2)
        self.assertEqual(html.count('aria-label="Postęp pobierania GGUF"'), 2)
