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
    adapter = (ROOT / 'src-tauri' / 'src' / 'credential_vault_native.rs').read_text()
    assert 'keyring::Entry::new' in adapter
    assert 'credential_vault_native::native()' in rust
    assert 'store_worker_token' in rust
    assert 'has_saved_worker_token' in rust
    assert 'forget_worker_token' in rust
    assert "invoke('store_worker_token',{token:field.value})" in html
    assert "invoke('forget_worker_token')" in html
    assert 'localStorage.setItem(token' not in html
    assert 'localStorage.setItem(field.value' not in html
    assert 'sessionStorage' not in html


def test_https_authentication_response_is_bounded_before_json_parsing():
    rust = (ROOT / 'src-tauri' / 'src' / 'lib.rs').read_text()
    auth = rust.split('async fn verify_worker_connection(', 1)[1].split('\n// Read-only, explicit job inspection.', 1)[0]
    assert 'const MAX_AUTH_RESPONSE_BYTES: usize = 4096;' in auth
    assert 'response.content_length()' in auth
    assert 'response.chunk().await' in auth
    assert 'chunk.len() > MAX_AUTH_RESPONSE_BYTES - bytes.len()' in auth
    assert 'serde_json::from_slice(&bytes)' in auth
    assert 'response.json().await' not in auth
