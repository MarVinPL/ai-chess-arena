"""Read-only native diagnostic UI contract and asynchronous behavior."""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "remote_worker_gui/public/index.html"

def test_native_diagnostic_ipc_is_read_only():
    source = HTML.read_text()
    rust = (ROOT / "remote_worker_gui/src-tauri/src/lib.rs").read_text()
    for command in ("inspect_phone_model_storage", "inspect_embedded_llama_engine", "inspect_phone_quic_protocol"):
        assert f"async fn {command}(" in rust
        assert f"invoke('{command}')" in source
    assert "initializeNativeDiagnostics" not in source
    assert "native_dispatch_enabled\": false" in rust
    assert 'id="nativeDiagnostics" hidden' in source

def test_native_diagnostics_success_failure_and_duplicate_click():
    subprocess.run(["node", "-e", r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync(process.argv[1], 'utf8');
const a = html.indexOf('const diagnosticText=');
const b = html.indexOf('const phoneStartButton=', a);
assert.ok(a >= 0 && b > a);
const elements = new Map(), pending = [], calls = [];
const get = id => {
 if (!elements.has(id)) elements.set(id,{textContent:'',disabled:false,addEventListener(){}});
 return elements.get(id);
};
const ctx = vm.createContext({document:{getElementById:get},window:{__TAURI__:{core:{invoke(name){
 calls.push(name); return new Promise((resolve,reject)=>pending.push({resolve,reject}));
}}}}});
vm.runInContext(html.slice(a,b),ctx);
const run = s => vm.runInContext(s,ctx);
(async()=>{
 assert.equal(calls.length,0);
 let work=run('inspectNativeDiagnostics()');
 assert.equal(get('checkNativeDiagnostics').disabled,true);
 await run('inspectNativeDiagnostics()');
 assert.equal(calls.length,2);
 pending[0].resolve({abi_available:true,legacy_generation_active:false});
 pending[1].resolve({free_bytes:2147483648,reserve_bytes:1073741824,available_for_models_bytes:1073741824,cached_models:2,cached_bytes:536870912});
 await work;
 assert.equal(get('ggufFree').textContent,'2.00 GiB');
 assert.equal(get('ggufInventory').textContent,'2 modeli · 0.50 GiB');
 assert.equal(get('checkNativeDiagnostics').disabled,false);
 work=run('inspectNativeDiagnostics()');
 pending[2].reject(Error('private path'));
 pending[3].resolve({free_bytes:-1,cached_models:-1});
 await work;
 assert.equal(get('ggufFree').textContent,'Nieznane');
 assert.equal(get('ggufInventory').textContent,'Nieznane');
 assert.equal(get('nativeRuntimeStatus').textContent,'Nieznany');
 assert.equal(get('checkNativeDiagnostics').disabled,false);
 for(const [protocol,label] of [['h3','QUIC / HTTP3'],['h2','HTTPS / HTTP2'],['http/1.1','HTTPS / HTTP1.1'],['invalid','Nieznany protokół']]){
  work=run('inspectQuicProtocol()');
  const count=calls.length;
  await run('inspectQuicProtocol()');
  assert.equal(calls.length,count);
  pending.at(-1).resolve(protocol);await work;
  assert.equal(get('quicStatus').textContent,label);
  assert.equal(get('checkQuicProtocol').disabled,false);
 }
 work=run('inspectQuicProtocol()');pending.at(-1).reject(Error('network'));await work;
 assert.equal(get('quicStatus').textContent,'Próba niedostępna / błąd sieci');
 assert.equal(get('checkQuicProtocol').disabled,false);
 assert.ok(calls.every(c=>['inspect_embedded_llama_engine','inspect_phone_model_storage','inspect_phone_quic_protocol'].includes(c)));
})().catch(e=>{console.error(e);process.exitCode=1;});
""", str(HTML)], check=True)
