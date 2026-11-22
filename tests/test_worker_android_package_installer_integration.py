"""Native Android installer integration checks (Android CI compiles the real code)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUI = (ROOT / "remote_worker_gui/public/index.html").read_text(encoding="utf-8")
LIB = (ROOT / "remote_worker_gui/src-tauri/src/lib.rs").read_text(encoding="utf-8")
RECEIVER = (ROOT / "remote_worker_gui/android/WorkerInstallStatusReceiver.kt").read_text(encoding="utf-8")
INJECTOR = (ROOT / "remote_worker_gui/android/inject_package_installer.py").read_text(encoding="utf-8")
WORKFLOW = (ROOT / ".github/workflows/android-worker-apk.yml").read_text(encoding="utf-8")
INSTALLER = (ROOT / "remote_worker_gui/src-tauri/src/android_update_package_installer.rs").read_text(encoding="utf-8")


def test_real_android_commit_and_explicit_receiver_are_built():
    assert '"commit"' in INSTALLER
    assert '"getBroadcast"' in INSTALLER
    assert "FLAG_MUTABLE" in INSTALLER
    assert 'class WorkerInstallStatusReceiver : BroadcastReceiver()' in RECEIVER
    assert 'PackageInstaller.STATUS_PENDING_USER_ACTION' in RECEIVER
    assert 'confirmation.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)' in RECEIVER
    assert '.commit()' in RECEIVER
    assert 'receiver.set(A + "exported", "false")' in INJECTOR
    assert '"android.permission.REQUEST_INSTALL_PACKAGES"' in INJECTOR
    assert "python3 android/inject_package_installer.py" in WORKFLOW
    assert WORKFLOW.index("python3 android/inject_package_installer.py") < WORKFLOW.index("npx tauri android build")


def test_native_rust_coordinates_install_with_journal_and_os_version():
    assert 'async fn install_downloaded_worker_apk(' in LIB
    assert 'async fn reconcile_installed_worker_update()' in LIB
    assert "fn inspect_android_install_result()" in LIB
    assert "android_update_coordinator::reserve_update(" in LIB
    assert "android_update_install_journal::save(&journal)" in LIB
    assert LIB.index("android_update_install_journal::save(&journal)") < LIB.index("commit_staged_session(session_id)")
    assert "inspect_installed_worker_identity()" in LIB
    assert "installed_update_matches_journal(" in LIB
    assert "android_update_coordinator::release_update(" in LIB


def test_user_visible_install_button_is_real_command_not_fake_success():
    assert 'id="installWorkerApk"' in GUI
    assert "invoke('install_downloaded_worker_apk'" in GUI
    assert "invoke('reconcile_installed_worker_update')" in GUI
    assert "installApkButton.disabled=true" in GUI
    assert "identity.installer_permission===true" in GUI
    assert 'Przekazano APK do instalatora Android' in GUI
    assert 'instalacja' in GUI.lower()
