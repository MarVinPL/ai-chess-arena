"""Fail-closed source wiring checks for Android PackageInstaller staging.

These checks supplement, but do not replace, an Android compilation and
physical-device installation test.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = (ROOT / "remote_worker_gui/src-tauri/src/android_update_package_installer.rs").read_text(encoding="utf-8")
LIB = (ROOT / "remote_worker_gui/src-tauri/src/lib.rs").read_text(encoding="utf-8")


def test_staging_is_internal_and_requires_identity_reservation_and_digest():
    assert "pub(crate) fn stage_verified_apk_session(" in MODULE
    assert "if !verified_identity || !coordinator_reserved" in MODULE
    assert "expected_sha256.len() != 64" in MODULE
    assert 'format!("{:x}", digest.finalize()) != expected_sha256' in MODULE
    assert "std::fs::symlink_metadata(apk)" in MODULE
    assert "meta.file_type().is_file()" in MODULE
    assert 'if observe_own_session()? != SessionObservation::NoVisibleSession' in MODULE


def test_native_session_stages_bytes_and_commits_with_explicit_os_callback():
    assert '"createSession"' in MODULE
    assert '"openSession"' in MODULE
    assert '"openWrite"' in MODULE
    assert '"write", "([BII)V"' in MODULE
    assert '"fsync"' in MODULE
    assert '"abandon"' in MODULE
    assert '"commit"' in MODULE
    assert '"getBroadcast"' in MODULE
    assert '"getIntentSender"' in MODULE
    assert 'WorkerInstallStatusReceiver' in MODULE
    assert 'FLAG_MUTABLE' in MODULE
    assert "stage_verified_apk_session" in LIB
    assert "commit_staged_session(session_id)" in LIB
    assert "android_update_install_journal::save(&journal)" in LIB
    assert LIB.index("android_update_install_journal::save(&journal)") < LIB.index("commit_staged_session(session_id)")
    assert "fn update_installation_readiness()" in LIB
    assert "VerifiedTaskState::Unknown, false, false" in LIB
