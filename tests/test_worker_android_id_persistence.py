"""Manually entered Worker IDs must not disappear when Android APK is upgraded."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UI = (ROOT / "remote_worker_gui/public/index.html").read_text()


def test_registered_worker_id_is_persisted_not_replaced_by_generated_id():
    assert "const workerIdentityKey='arena-worker-installation-id-v1';" in UI
    assert "function validWorkerIdentity(id)" in UI
    assert "/^[A-Za-z0-9_-]{8,96}$/.test(id)" in UI
    assert "function persistWorkerIdentity()" in UI
    assert "localStorage.setItem(workerIdentityKey,id)" in UI
    assert "workerIdField.addEventListener('change',persistWorkerIdentity)" in UI
    assert "if(ok && !persistWorkerIdentity())" in UI
    assert "if(!validWorkerIdentity(id)){" in UI
    assert "workerIdField.value=id" in UI


def test_token_cannot_leak_into_localstorage():
    assert "localStorage.setItem(workerIdentityKey,token)" not in UI
    assert "localStorage.setItem('token'" not in UI
    assert "store_worker_token" in UI
    assert "verify_worker_connection" in UI
    assert 'android_marvin_01' in UI  # documented example accepted by pattern
