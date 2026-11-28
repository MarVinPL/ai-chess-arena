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
        if name == "store_worker_token":
            android = section.split('#[cfg(target_os = "android")]', 1)[1]
            assert android.index("authenticate_registration") < android.index(".await?") < android.index("credential_change_gate()?")
            assert android.index("credential_change_gate()?") < android.index("insert_registration_token")
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
    assert journal.count("*guard = true") == 3
    assert journal.count("dir(&require_pair()?, slot)") == 3
    stage = journal.split("fn set_state(", 1)[1].split("trait Store", 1)[0]
    assert stage.index("f.sync_all()") < stage.index("File::open(dir)") < stage.index("fs::rename")
    status = (SRC / "lib.rs").read_text().split("fn update_recovery_status()", 1)[1].split("#[tauri::command]", 1)[0]
    assert status.index("credential_change_gate()") < status.index("android_update_install_journal::load()")
    assert "blocked: true" in status
    save = (SRC / "android_update_install_journal.rs").read_text().split("pub(crate) fn save(", 1)[1].split("pub(crate) fn clear_confirmed", 1)[0]
    assert "android_update_recovery::load_pending()?.is_none()" in save


def test_android_initialization_ui_leaves_native_proof_and_secrets_in_rust():
    ui = (ROOT / "public/index.html").read_text()
    lib = (SRC / "lib.rs").read_text()
    handler = ui.split("initializeUpdateJournals.addEventListener('click'", 1)[1]
    handler = handler.split("document.getElementById('inspectUpdateHold')", 1)[0]
    assert "invoke('initialize_update_journals',{server:target.server,workerId:target.workerId})" in handler
    assert handler.index("connectionIsCurrent(target)") < handler.index("Dzienniki są już zainicjalizowane")
    assert "refreshUpdateRecoveryStatus()" in handler
    native = lib.split("async fn initialize_update_journals(", 1)[1].split("// Recovery is read-only", 1)[0]
    assert "server: String, worker_id: String" in native
    assert '#[cfg(not(target_os = "android"))]' in native
    assert native.index("generation_active()") < native.index("JournalBaseline::inspect()")
    assert native.index("JournalBaseline::inspect()") < native.index("reconcile_journal_initialization")
    assert "release_update(" not in native
    assert "initialize_update_journals" in lib.split("tauri::generate_handler![", 1)[1]
