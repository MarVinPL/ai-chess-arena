"""Guards for the fail-closed native Android APK release asset metadata gate.

These tests do not authenticate APK bytes or authorize installation.
The Rust module also contains unit tests for rejected candidate metadata.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "remote_worker_gui/src-tauri/src/lib.rs"
GATE = ROOT / "remote_worker_gui/src-tauri/src/android_update_asset.rs"


def test_asset_gate_is_built_into_native_worker():
    assert "mod android_update_asset;" in LIB.read_text(encoding="utf-8")
    gate = GATE.read_text(encoding="utf-8")
    assert "fn select_apk_candidate(" in gate
    assert "fn exact_release_download_url(" in gate
    assert "MAX_APK_BYTES: u64 = 200 * 1024 * 1024" in gate
    assert "MIN_APK_BYTES: u64 = 1024" in gate
    assert 'Some("github.com")' in gate
    assert '"/MarVinPL/ai-chess-arena/releases/download/{tag}/{filename}"' in gate
    assert 'url.scheme() == "https"' in gate
    assert 'url.query().is_none()' in gate
    assert 'url.fragment().is_none()' in gate
    assert 'asset.get("state")?.as_str()? != "uploaded"' in gate
    assert "candidates.next().is_some()" in gate


def test_metadata_gate_does_not_gain_download_or_install_privileges():
    gate = GATE.read_text(encoding="utf-8")
    for forbidden in ("reqwest::Client", "std::fs::write", "std::process::Command",
                      "install_package", "install_update", "download_and_install",
                      "Intent::ACTION_VIEW", "FileProvider"):
        assert forbidden not in gate
    assert "A validated candidate is NOT permission to install." in gate
    lib = LIB.read_text(encoding="utf-8")
    assert "may_install_update(VerifiedTaskState::Unknown, false, false)" in lib


def test_rust_unit_cases_reject_url_tampering_ambiguous_assets_and_downgrade_candidates():
    gate = GATE.read_text(encoding="utf-8")
    assert "fn rejects_wrong_tags_urls_and_insecure_hosts()" in gate
    assert "fn rejects_ambiguous_missing_or_outsize_assets()" in gate
    assert "fn rejects_wrong_platform_pending_and_draft()" in gate
    assert "select_apk_candidate(&release(), \"worker-v1.2.4\").is_none()" in gate
    assert "fn rejects_prereleases_missing_flags_and_malformed_worker_tags()" in gate
    assert "crate::numeric_version(version)?" in gate
    assert 'release.get("prerelease")?.as_bool()?' in gate


def test_apk_stream_verification_is_bounded_and_fail_closed():
    gate = GATE.read_text(encoding="utf-8")
    manifest = (ROOT / "remote_worker_gui/src-tauri/Cargo.toml").read_text(encoding="utf-8")
    lock = (ROOT / "remote_worker_gui/src-tauri/Cargo.lock").read_text(encoding="utf-8")
    assert 'sha2 = "=0.10.9"' in manifest
    assert '"sha2 0.10.9"' in lock
    assert "pub(crate) fn verify_apk_stream<R: Read>(" in gate
    assert "Sha256::new()" in gate
    assert "let mut buffer = [0u8; 64 * 1024]" in gate
    assert "if bytes_read > candidate.size { return Ok(false); }" in gate
    assert "if bytes_read != candidate.size { return Ok(false); }" in gate
    assert "Ok(actual == candidate.expected_sha256)" in gate
    assert "fn streaming_digest_checks_exact_size_and_bytes()" in gate
    assert "fn streaming_digest_fails_closed_for_bad_metadata_and_read_errors()" in gate
    assert "This is only an integrity check, NOT APK certificate validation" in gate
    assert "download_and_install" not in gate


def test_native_release_checker_actually_uses_candidate_gate_and_displays_result():
    """Integration wiring guard: selector must be invoked by a Tauri command, not dead code."""
    lib = LIB.read_text(encoding="utf-8")
    ui = (ROOT / "remote_worker_gui/public/index.html").read_text(encoding="utf-8")
    start = lib.index("async fn check_worker_release()")
    end = lib.index("// Fail closed: a missing coordinator confirmation", start)
    release_command = lib[start:end]
    assert "android_update_asset::select_apk_candidate(data, tag).is_some()" in release_command
    assert "apk_asset_available: bool" in lib
    assert "let apk_asset_available = latest > installed" in release_command
    assert "apk_asset_available: false" in release_command
    fetch_start = lib.index("async fn fetch_worker_releases()")
    fetch_end = lib.index("async fn check_worker_release()", fetch_start)
    fetch = lib[fetch_start:fetch_end]
    assert "bounded GitHub metadata" in fetch
    assert "MAX_RELEASE_JSON: usize = 1024 * 1024" in fetch
    assert "if chunk.len() > MAX_RELEASE_JSON - body.len()" in fetch
    assert "serde_json::from_slice(&body)" in fetch
    assert "info.apk_asset_available===true" in ui
    assert "Brak kwalifikującego się pliku APK ARM64" in ui
    assert "Instalacja pozostaje wyłączona." in ui
    assert "invoke('download_worker_apk')" in ui
    assert "invoke('install_update')" not in ui
    assert "invoke('check_worker_release')" in ui
    assert "invoke('download_update')" not in ui


def test_download_is_real_native_command_with_private_verified_staging():
    lib = LIB.read_text(encoding="utf-8")
    source = (ROOT / "remote_worker_gui/src-tauri/src/android_update_download.rs").read_text(encoding="utf-8")
    ui = (ROOT / "remote_worker_gui/public/index.html").read_text(encoding="utf-8")
    start = lib.index("async fn download_worker_apk(app:")
    end = lib.index("// Fail closed: a missing coordinator confirmation", start)
    command = lib[start:end]
    assert "mod android_update_download;" in lib
    assert "download_worker_apk, check_update_safety" in lib
    assert "let releases = fetch_worker_releases().await?" in command
    assert "android_update_asset::select_apk_candidate(release, tag)" in command
    assert "android_update_download::stage_candidate(&app, candidate, tag).await" in command
    assert '#[cfg(target_os = "android")]' in command
    assert "async fn download_apk_to_private_dir" in source
    assert "app.path().app_cache_dir()" in source
    assert "OpenOptions::new().write(true).create_new(true)" in source
    assert "verify_apk_stream(&mut staged, candidate)" in source
    assert 'fs::rename(&temporary, target)' in source
    assert "if result.is_err()" in source
    assert "fs::remove_file(&temporary)" in source
    assert "if received > candidate.size" in source
    assert "received != candidate.size" in source
    assert "release-assets.githubusercontent.com" in source
    assert "objects.githubusercontent.com" in source
    assert "attempt.previous().len() > 3" in source
    assert "DOWNLOAD_IN_PROGRESS.compare_exchange(false, true" in source
    assert "fn only_one_staging_transfer_can_hold_the_lock()" in source
    assert "fs::symlink_metadata(&target)" in source
    assert "verify_apk_stream(&mut f, &candidate)" in source
    assert "fs::remove_file(&partial)" in source
    assert "Pobierz APK" in ui
    assert "apkDownloadProgress" in ui
    assert "worker-apk-download-progress" in ui
    assert "download_worker_apk" in ui
    assert "Instalacja nadal wyłączona" in ui
    assert "install_update" not in source
    assert "PackageInstaller" not in source


def test_existing_worker_signing_certificate_is_pinned_in_native_identity_gate():
    identity = (ROOT / "remote_worker_gui/src-tauri/src/android_update_identity.rs").read_text(encoding="utf-8")
    workflow = (ROOT / ".github/workflows/android-worker-apk.yml").read_text(encoding="utf-8")
    configuration = (ROOT / "remote_worker_gui/src-tauri/tauri.conf.json").read_text(encoding="utf-8")
    expected = "3f0449ec3a2399ec669cce2dcbd6aa7262671345dfb983c33e2cadf4d1b0da12"
    assert expected in identity and expected in workflow
    assert 'WORKER_SIGNING_CERT_SHA256' in identity
    assert 'pl.marvin.aichessarena.worker' in identity
    assert 'pl.marvin.aichessarena.worker' in configuration
    assert "installed.certificate_sha256 != WORKER_SIGNING_CERT_SHA256" in identity
    assert "candidate.certificate_sha256 != WORKER_SIGNING_CERT_SHA256" in identity
    assert "fn rejects_two_matching_but_untrusted_certificates()" in identity
    assert "fn rejects_when_only_installed_certificate_differs_from_pinned_key()" in identity
    # Platform record is not yet populated from PackageManager; no install gate bypass.
    assert 'may_install_update(VerifiedTaskState::Unknown, false, false)' in LIB.read_text(encoding="utf-8")


def test_android_packagemanager_verifies_real_downloaded_apk_before_reporting_success():
    lib = LIB.read_text(encoding="utf-8")
    ui = (ROOT / "remote_worker_gui/public/index.html").read_text(encoding="utf-8")
    bridge = (ROOT / "remote_worker_gui/src-tauri/src/android_update_package_manager.rs").read_text(encoding="utf-8")
    manifest = (ROOT / "remote_worker_gui/src-tauri/Cargo.toml").read_text(encoding="utf-8")
    lock = (ROOT / "remote_worker_gui/src-tauri/Cargo.lock").read_text(encoding="utf-8")
    assert 'jni = "=0.21.1"' in manifest
    assert 'ndk-context = "=0.1.1"' in manifest
    assert '"jni"' in lock and '"ndk-context"' in lock
    assert '#[cfg(target_os = "android")]\nmod android_update_package_manager;' in lib
    assert 'verify_downloaded_worker_apk, check_update_safety' in lib
    start = lib.index("async fn verify_downloaded_worker_apk(")
    end = lib.index("// Fail closed: a missing coordinator confirmation", start)
    command = lib[start:end]
    for required in (
        "fetch_worker_releases().await?",
        "select_apk_candidate(release, tag)",
        "app.path().app_cache_dir()",
        "std::fs::symlink_metadata(&apk)",
        "verify_apk_stream(&mut file, &candidate)",
        "inspect_staged_worker_apk(&apk)",
        "identity == android_update_identity::IdentityCheck::Verified",
    ):
        assert required in command
    assert 'getPackageManager' in bridge
    assert 'getPackageInfo' in bridge
    assert 'getPackageArchiveInfo' in bridge
    assert 'getLongVersionCode' in bridge
    assert 'getApkContentsSigners' in bridge
    assert 'hasMultipleSigners' in bridge
    assert 'GET_SIGNING_CERTIFICATES: i32 = 0x08000000' in bridge
    assert 'check_platform_apk_identity(&installed_identity, &candidate_identity)' in bridge
    assert "invoke('verify_downloaded_worker_apk')" in ui
    assert "Instalacja nadal wyłączona" in ui
    assert "install_update" not in bridge
    assert "PackageInstaller" not in bridge
