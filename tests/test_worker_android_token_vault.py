"""Android Worker machine token must survive process death and APK updates.

Static/injection checks cannot prove OS-backed key persistence on a device;
that requires the final Android E2E test.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
GUI = ROOT / "remote_worker_gui"
KOTLIN = (GUI / "android/WorkerCredentialVault.kt").read_text()
NATIVE = (GUI / "src-tauri/src/android_worker_token_store.rs").read_text()
LIB = (GUI / "src-tauri/src/lib.rs").read_text()
ADAPTER = (GUI / "src-tauri/src/credential_vault_native.rs").read_text()
VAULT = (GUI / "src-tauri/src/credential_vault.rs").read_text()
INJECTOR = GUI / "android/inject_package_installer.py"


def test_android_never_uses_mock_keyring_v3_and_keeps_token_out_of_webview():
    assert '#[cfg(not(target_os = "android"))]\npub(crate) type NativeBackend = keyring::Entry;' in ADAPTER
    assert '#[cfg(target_os = "android")]\npub(crate) type NativeBackend' in ADAPTER
    assert "android_worker_token_store::AndroidTokenEntry" in ADAPTER
    assert "mod android_worker_token_store;" in LIB
    assert "token_entry()?.get_password()" in LIB
    assert "token_entry()?.set_password(" in LIB
    assert 'fn has_saved_worker_token() -> bool' in LIB
    assert "never use keyring" in ADAPTER.lower()
    assert "WorkerCredentialVault" in NATIVE
    assert "getClassLoader" in NATIVE and "loadClass" in NATIVE
    assert "env.call_static_method(" in NATIVE
    assert "read_optional" in NATIVE and "set_password" in NATIVE
    assert "delete_credential" in NATIVE
    assert "localStorage.setItem(" not in KOTLIN


def test_secure_android_token_is_encrypted_authenticated_and_persistent():
    for expected in [
        "AndroidKeyStore",
        "AES/GCM/NoPadding",
        "KeyGenParameterSpec.Builder(",
        "KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT",
        "GCMParameterSpec(TAG_BITS, iv)",
        "Context.MODE_PRIVATE",
        ".commit()",
        "Base64.encodeToString",
        "cipher.doFinal(",
        'fun save(context: Context, token: String)',
        'fun load(context: Context): String?',
        'fun clear(context: Context)',
    ]:
        assert expected in KOTLIN
    assert "return null" in KOTLIN
    assert "catch (" not in KOTLIN
    assert "MODE_WORLD_READABLE" not in KOTLIN
    assert "EncryptedSharedPreferences" not in KOTLIN
    assert "Log." not in KOTLIN and "System.out" not in KOTLIN
    assert "getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE)" in KOTLIN


def test_generated_android_project_copies_the_token_vault(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("inject", INJECTOR)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    main = tmp_path / "gen/android/app/src/main"
    main.mkdir(parents=True)
    (main / "AndroidManifest.xml").write_text(
        '<manifest xmlns:android="http://schemas.android.com/apk/res/android" '
        'package="pl.marvin.aichessarena.worker"><application /></manifest>'
    )
    dest = main / "java/pl/marvin/aichessarena/worker"
    monkeypatch.setattr(mod, "MANIFEST", main / "AndroidManifest.xml")
    monkeypatch.setattr(mod, "TARGET", dest / "WorkerInstallStatusReceiver.kt")
    monkeypatch.setattr(mod, "VAULT_TARGET", dest / "WorkerCredentialVault.kt")
    mod.install()
    assert (dest / "WorkerCredentialVault.kt").read_text() == KOTLIN
    assert (dest / "WorkerInstallStatusReceiver.kt").is_file()
    tree = ET.parse(main / "AndroidManifest.xml")
    names = [r.attrib.get("{http://schemas.android.com/apk/res/android}name")
             for r in tree.findall("./application/receiver")]
    assert names == ["pl.marvin.aichessarena.worker.WorkerInstallStatusReceiver"]


def test_android_upgrade_keeps_original_server_token_and_never_claims_migration():
    section = ADAPTER.split("fn native()", 1)[1]
    assert "delete_credential" not in section
    assert "http://" not in KOTLIN


def test_vault_serializes_and_guards_mutations():
    for method, mutation in (("set_password", "self.backend.write"), ("delete_credential", "self.backend.delete")):
        section = "".join(VAULT.split("pub(crate) fn " + method, 1)[1].split())
        assert section.index("self.operations.lock()") < section.index("check()?") < section.index(mutation)
    assert "tauri::command" not in VAULT
    assert "tauri::command" not in ADAPTER
    assert 'keyring::Entry::new("pl.marvin.aichessarena.worker", "worker-token")' in ADAPTER

def test_missing_key_and_corrupt_ciphertext_are_not_reset():
    assert 'key(false)' in KOTLIN
    assert 'key(!prefs.contains(PREF_TOKEN))' in KOTLIN
    assert 'if (prefs.contains(PREF_TOKEN)) check(load(context) != null)' in KOTLIN
    assert 'check(allowCreate)' in KOTLIN
    clear = KOTLIN.split('fun clear(context: Context)', 1)[1]
    assert clear.index('load(context)') < clear.index('.edit().remove(PREF_TOKEN)')
    assert 'exception_clear()' in NATIVE
    assert 'Ok(None)' in NATIVE
    assert 'Err(keyring::Error::NoEntry) => Ok(None)' in ADAPTER

def test_token_state_ipc_distinguishes_absence_and_failure():
    state = LIB.split('fn worker_token_state()', 1)[1].split('#[tauri::command]', 1)[0]
    assert 'Ok(Some(_)) => "saved"' in state
    assert 'Ok(None) => "missing"' in state
    assert 'Err(_) => "unavailable"' in state
    html = (GUI / 'public/index.html').read_text()
    assert "invoke('worker_token_state')" in html
    assert "if(state!=='saved')" in html


def test_failed_commit_blocks_access_and_stale_status_is_ignored():
    assert 'check(!uncertain)' in KOTLIN
    for method in ('save', 'clear'):
        section = KOTLIN.split('fun ' + method + '(', 1)[1].split('@JvmStatic', 1)[0]
        assert section.index('uncertain = true') < section.index('.commit()') < section.index('uncertain = false')
    html = (GUI / 'public/index.html').read_text()
    assert 'if(revision!==tokenStateRevision)return;' in html


def test_credential_regression_preserves_legacy_identity_and_no_migration_dependency():
    import tomllib
    manifest = tomllib.loads((GUI / 'src-tauri/Cargo.toml').read_text())
    lock = tomllib.loads((GUI / 'src-tauri/Cargo.lock').read_text())
    assert not {'tauri-plugin-keyring-store', 'android-native-keyring-store'} & {p['name'] for p in lock['package']}
    assert 'patch' not in manifest or 'android-native-keyring-store' not in manifest['patch'].get('crates-io', {})
    assert '"worker_machine_credential_v1"' in KOTLIN
    assert '"worker_token_aes_gcm"' in KOTLIN
    assert '"worker_update_journals_v1"' in KOTLIN
    assert 'noBackupFilesDir' in KOTLIN
    assert 'mod credential_migration;' not in LIB


def test_android_registration_and_deletion_keep_change_gate_and_journal_checks():
    registration = LIB.split('async fn store_worker_token(', 1)[1].split('#[tauri::command]', 1)[0]
    android = registration.split('#[cfg(target_os = "android")]', 1)[1]
    assert android.index('authenticate_registration') < android.index('.await?') < android.index('credential_change_gate()?') < android.index('insert_registration_token')
    assert 'credential_journal::check_registration_storage' in android
    deletion = LIB.split('fn forget_worker_token()', 1)[1].split('#[tauri::command]', 1)[0]
    assert deletion.index('credential_change_gate()?') < deletion.index('delete_credential(ensure_no_pending_update)')
    guard = LIB.split('fn ensure_no_pending_update()', 1)[1].split('#[derive', 1)[0]
    assert 'android_update_install_journal::load()?' in guard
    assert 'android_update_recovery::load_pending()' in guard
    assert 'Err(_) => Err(' in guard
