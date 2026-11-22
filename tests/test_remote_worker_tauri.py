import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / 'remote_worker_gui'


def test_tauri_scaffold_configuration():
    cfg = json.loads((ROOT / 'src-tauri' / 'tauri.conf.json').read_text())
    caps = json.loads((ROOT / 'src-tauri' / 'capabilities' / 'default.json').read_text())
    assert cfg['build']['frontendDist'] == '../public'
    assert cfg['app']['security']['csp'].find("connect-src 'none'") != -1
    assert cfg['bundle']['active'] is False
    assert caps['permissions'] == ['core:default']
    assert (ROOT / 'src-tauri' / 'src' / 'lib.rs').exists()


def test_no_remote_commands_enabled():
    code = (ROOT / 'src-tauri' / 'src' / 'lib.rs').read_text()
    assert 'worker_status' in code
    assert 'std::process::Command' not in code
    assert 'reqwest' in code  # HTTPS authentication/status checks only
    assert 'verify_worker_connection' in code
    assert 'check_update_safety' in code
    assert 'Command::new' not in code


def test_local_status_bridge_is_read_only():
    cfg = json.loads((ROOT / 'src-tauri' / 'tauri.conf.json').read_text())
    html = (ROOT / 'public' / 'index.html').read_text()
    assert cfg['app']['withGlobalTauri'] is True
    assert "invoke('worker_status')" in html
    assert "invoke('pair')" not in html
    assert "invoke('start')" not in html


def test_token_onboarding_does_not_persist_plaintext():
    html = (ROOT / 'public' / 'index.html').read_text()
    assert 'Token dostępu' in html
    assert 'id="code" type="password"' in html
    assert 'id="rememberToken"' in html
    assert 'localStorage.setItem(updatePrefKey' in html
    assert 'localStorage.setItem(updateLastKey' in html
    assert 'localStorage.setItem(token' not in html
    assert 'localStorage.setItem(field.value' not in html
    assert 'sessionStorage' not in html
    assert 'id="pair" type="button"' in html


def test_native_token_storage_never_exposes_secret():
    rust = (ROOT / 'src-tauri' / 'src' / 'lib.rs').read_text()
    html = (ROOT / 'public' / 'index.html').read_text()
    assert '.plugin(tauri_plugin_keyring_store::init())' in rust
    assert 'app.keyring().store.set_password' in rust
    assert 'app.keyring().store.delete' in rust
    assert 'read_saved_worker_token(app).await?' in rust
    assert 'keyring::Entry::new' not in rust
    assert 'store_worker_token' in rust
    assert 'has_saved_worker_token' in rust
    assert 'forget_worker_token' in rust
    assert "invoke('store_worker_token',{token:field.value})" in html
    assert "invoke('forget_worker_token')" in html
    assert 'localStorage.setItem(token' not in html
    assert 'localStorage.setItem(field.value' not in html
    assert 'sessionStorage' not in html


def test_android_uses_native_keystore_with_no_plaintext_fallback():
    cargo = (ROOT / 'src-tauri' / 'Cargo.toml').read_text()
    rust = (ROOT / 'src-tauri' / 'src' / 'lib.rs').read_text()
    capabilities = json.loads((ROOT / 'src-tauri' / 'capabilities' / 'default.json').read_text())
    assert 'tauri-plugin-keyring-store = { version = "=0.3.2", default-features = false }' in cargo
    assert 'keyring = { version = "3.6"' not in cargo
    assert 'get_password(TOKEN_ACCOUNT)' in rust
    assert 'has_saved_worker_token(app: tauri::AppHandle) -> Result<bool, String>' in rust
    assert 'spawn_blocking' in rust
    # Only our backend commands see the token. Do not grant JS broad plugin IPC.
    assert 'keyring-store:default' not in capabilities['permissions']


def test_keystore_deletion_is_scoped_to_single_worker_account():
    rust = (ROOT / 'src-tauri' / 'src' / 'lib.rs').read_text()
    caps = json.loads((ROOT / 'src-tauri' / 'capabilities' / 'default.json').read_text())
    assert 'const TOKEN_ACCOUNT: &str = "worker-token";' in rust
    assert rust.count('.store.delete(') == 1
    assert '.store.delete(TOKEN_ACCOUNT)' in rust
    assert 'forget_worker_token(app: tauri::AppHandle)' in rust
    assert not any(method in rust for method in (
        '.store.clear(', '.store.reset(', '.store.delete_all(',
        '.store.delete_passwords(', 'KeyStore.deleteEntry(',
    ))
    # Webview must not obtain broad permission to mutate the native keyring.
    assert not any('keyring-store' in entry for entry in caps['permissions'])
