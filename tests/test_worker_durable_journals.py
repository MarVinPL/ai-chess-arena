from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "remote_worker_gui"
SRC = ROOT / "src-tauri/src"

def test_android_journal_slots_are_native_and_private():
    for filename, slot in (("android_update_recovery.rs", "PendingHold"),
                           ("android_update_install_journal.rs", "PendingInstall")):
        code = (SRC / filename).read_text()
        assert '#[cfg(not(target_os = "android"))]\nfn entry()' in code
        assert f'credential_journal::read(crate::credential_vault::Slot::{slot})' in code
        assert 'credential_journal::insert' in code
        assert 'credential_journal::delete_expected' in code
    kotlin = (ROOT / "android/WorkerCredentialVault.kt").read_text()
    assert 'journalKey(name, false)' in kotlin
    assert 'noBackupFilesDir' in kotlin
    assert 'cipher.updateAAD(name.toByteArray' in kotlin
    assert 'check(journalLoad(context, name) == expected)' in kotlin
    assert 'journalInsert' not in (SRC / "lib.rs").read_text()

def test_change_gate_fences_reservation_and_token_mutations():
    lib = (SRC / "lib.rs").read_text()
    for name in ("store_worker_token", "forget_worker_token"):
        section = lib.split("fn " + name, 1)[1].split("#[tauri::command]", 1)[0]
        assert section.index("credential_change_gate()?") < section.index("token_entry()?")
    guard = lib.split("fn ensure_no_pending_update()", 1)[1].split("#[derive", 1)[0]
    assert "android_update_install_journal::load()?" in guard
    coordinator = (SRC / "android_update_coordinator.rs").read_text().split("async fn reserve_update", 1)[1]
    assert coordinator.index("credential_change_gate()?") < coordinator.index("get_password()?") < coordinator.index("save_pending(&pending)?") < coordinator.index(".send().await")
    journal = (SRC / "credential_journal.rs").read_text()
    assert "State::Uncertain | State::Uninitialized" in journal
    assert "f.sync_all()" in journal
    assert "credential_migration" not in journal
    assert "initialize_empty" not in lib

def test_failed_marker_commit_fences_process_and_install_status():
    journal = (SRC / "credential_journal.rs").read_text()
    assert "Mutex<bool>" in journal
    assert journal.count("*guard = true") == 2
    stage = journal.split("fn set_state(", 1)[1].split("trait Store", 1)[0]
    assert stage.index("f.sync_all()") < stage.index("File::open(dir)") < stage.index("fs::rename")
    status = (SRC / "lib.rs").read_text().split("fn update_recovery_status()", 1)[1].split("#[tauri::command]", 1)[0]
    assert status.index("credential_change_gate()") < status.index("android_update_install_journal::load()")
    assert "blocked: true" in status
    save = (SRC / "android_update_install_journal.rs").read_text().split("pub(crate) fn save(", 1)[1].split("pub(crate) fn clear_confirmed", 1)[0]
    assert "android_update_recovery::load_pending()?.is_none()" in save
