"""Regression checks for durable Worker ID and native credential status (#239)."""
from pathlib import Path
import shutil
import subprocess

import pytest

HTML = (Path(__file__).resolve().parents[1] / "remote_worker_gui/public/index.html")


def test_id_is_saved_with_custom_values_and_original_storage_key():
    source = HTML.read_text(encoding="utf-8")
    assert "const workerIdentityKey='arena-worker-installation-id-v1'" in source
    assert "function validWorkerId(id)" in source
    assert "^[A-Za-z0-9_-]{8,96}$" in source
    assert "function persistWorkerId()" in source
    assert "localStorage.setItem(workerIdentityKey,id)" in source
    assert "localStorage.getItem(workerIdentityKey)!==id" in source
    assert "workerIdField.addEventListener('change',persistWorkerId)" in source
    assert "getElementById('saveWorkerId').addEventListener('click',persistWorkerId)" in source
    assert "if(!persistWorkerId())" in source
    assert "if(!id || !/^android_" not in source


def test_token_diagnostics_do_not_store_or_display_secret_in_webview():
    source = HTML.read_text(encoding="utf-8")
    assert 'id="tokenStatus"' in source
    assert "const tokenMessage=document.getElementById('tokenStatus')" in source
    assert "has_saved_worker_token" in source
    assert "pole hasła pozostaje puste" in source
    assert "To nie oznacza usunięcia tokenu" in source
    assert "localStorage.setItem(token" not in source
    assert "localStorage.setItem(field.value" not in source
    assert "sessionStorage" not in source


def test_identity_lifecycle_in_node_vm():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is unavailable; static identity tests still apply")
    source = HTML.read_text(encoding="utf-8")
    snippet = "const workerIdentityKey=" + source.split("const workerIdentityKey=", 1)[1].split("const cpu=", 1)[0]
    script = r"""
const vm=require('node:vm');
const assert=require('node:assert/strict');
let source='';
process.stdin.setEncoding('utf8');
process.stdin.on('data',chunk=>source+=chunk);
process.stdin.on('end',()=>{
  const key='arena-worker-installation-id-v1';
  function boot(original={},failWrite=false){
    const storage=new Map(Object.entries(original));
    const worker={value:'',handlers:{},addEventListener(k,fn){this.handlers[k]=fn;}};
    const message={textContent:''};
    const button={handlers:{},addEventListener(k,fn){this.handlers[k]=fn;}};
    const fakeDocument={getElementById(name){
      return {workerId:worker,workerIdFeedback:message,saveWorkerId:button}[name];
    }};
    const fakeStorage={
      getItem(k){return storage.has(k)?storage.get(k):null;},
      setItem(k,v){if(failWrite)throw Error('denied');storage.set(k,String(v));}
    };
    const random={getRandomValues(bytes){
      for(let i=0;i<bytes.length;i++)bytes[i]=i;return bytes;
    }};
    vm.runInNewContext(source,{document:fakeDocument,localStorage:fakeStorage,
      crypto:random,Uint8Array});
    return {storage,worker,message,button};
  }
  const fresh=boot();
  assert.match(fresh.worker.value,/^android_[0-9a-f]{32}$/);
  assert.equal(fresh.storage.get(key),fresh.worker.value);
  fresh.worker.value='my_computer_01';
  assert.equal(fresh.worker.handlers.change(),true);
  assert.equal(fresh.storage.get(key),'my_computer_01');
  const reopened=boot(Object.fromEntries(fresh.storage));
  assert.equal(reopened.worker.value,'my_computer_01');
  reopened.worker.value='computer-02';
  assert.equal(reopened.button.handlers.click(),true);
  assert.equal(reopened.storage.get(key),'computer-02');
  reopened.worker.value='invalid value with spaces';
  assert.equal(reopened.worker.handlers.change(),false);
  assert.equal(reopened.storage.get(key),'computer-02');
  const denied=boot({[key]:'computer-02'},true);
  denied.worker.value='computer-03';
  assert.equal(denied.worker.handlers.change(),false);
  assert.equal(denied.storage.get(key),'computer-02');
  assert.match(denied.message.textContent,/Nie udało się trwale zapisać/);
  const firstFailed=boot({},true);
  assert.equal(firstFailed.worker.value,'');
  assert.match(firstFailed.message.textContent,/Nie udało się odczytać lub zapisać/);
  console.log('identity lifecycle OK');
});
"""
    result = subprocess.run([node, "-e", script], input=snippet, text=True,
                            capture_output=True, timeout=15, check=False)
    assert result.returncode == 0, result.stderr
    assert "identity lifecycle OK" in result.stdout
