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
INJECTOR = GUI / "android/inject_package_installer.py"


def test_android_never_uses_mock_keyring_v3_and_keeps_token_out_of_webview():
    assert '#[cfg(not(target_os = "android"))]\nfn token_entry()' in LIB
    assert '#[cfg(target_os = "android")]\nfn token_entry()' in LIB
    assert "android_worker_token_store::AndroidTokenEntry" in LIB
    assert "mod android_worker_token_store;" in LIB
    assert "token_entry()?.get_password()" in LIB
    assert "token_entry()?.set_password(" in LIB
    assert 'fn has_saved_worker_token() -> bool' in LIB
    assert "never use keyring" in LIB.lower()
    assert "WorkerCredentialVault" in NATIVE
    assert "getClassLoader" in NATIVE and "loadClass" in NATIVE
    assert "env.call_static_method(" in NATIVE
    assert "get_password" in NATIVE and "set_password" in NATIVE
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
    section = LIB.split(
        "fn token_entry() -> Result<android_worker_token_store::AndroidTokenEntry", 1
    )[1].split("#[tauri::command]", 1)[0]
    assert "delete_credential" not in section
    assert "http://" not in KOTLIN
