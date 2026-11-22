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
    handler = lib.split("tauri::generate_handler![", 1)[1].split("]", 1)[0]
    for command_name in ("download_worker_apk", "verify_downloaded_worker_apk", "check_update_safety"):
        assert command_name in handler
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


def test_coordinator_idle_requires_authenticated_recent_nonce_bound_evidence():
    lib = LIB.read_text(encoding="utf-8")
    source = (ROOT / "remote_worker_gui/src-tauri/src/android_update_coordinator.rs").read_text(encoding="utf-8")
    manifest = (ROOT / "remote_worker_gui/src-tauri/Cargo.toml").read_text(encoding="utf-8")
    lock = (ROOT / "remote_worker_gui/src-tauri/Cargo.lock").read_text(encoding="utf-8")
    assert "mod android_update_coordinator;" in lib
    assert 'getrandom = "=0.2.17"' in manifest
    assert '"getrandom 0.2.17"' in lock
    start = lib.index("async fn check_update_safety(")
    end = lib.index("// Read-only release discovery", start)
    command = lib[start:end]
    assert 'android_update_coordinator::random_nonce()?' in command
    assert '"worker_id": worker_id, "token": token, "nonce": nonce' in command
    assert 'fresh_authenticated_idle(&data, &worker_id, &nonce, now)' in command
    assert "MAX_IDLE_AGE_MS: u64 = 30_000" in source
    assert "MAX_FUTURE_SKEW_MS: u64 = 5_000" in source
    assert 'getrandom::getrandom(&mut bytes)' in source
    for field in ("authenticated", "source", "worker_id", "nonce", "active_tasks", "checked_at_unix_ms"):
        assert f'response.get("{field}")' in source
    for scenario in ("rejects_replay_stale_future_wrong_worker_and_tasks",
                     "rejects_legacy_or_incomplete_server_answers"):
        assert f"fn {scenario}()" in source
    # Despite read-only idle checks, installation is never automatically enabled.
    assert "may_install_update(VerifiedTaskState::Unknown, false, false)" in lib


def test_android_installer_permission_is_read_only_and_fails_closed():
    lib = LIB.read_text(encoding="utf-8")
    bridge = (ROOT / "remote_worker_gui/src-tauri/src/android_update_package_manager.rs").read_text(encoding="utf-8")
    ui = (ROOT / "remote_worker_gui/public/index.html").read_text(encoding="utf-8")
    assert "fn can_request_package_installation()" in bridge
    assert 'canRequestPackageInstalls' in bridge
    assert "installer_permission: bool" in lib
    assert "can_request_package_installation().unwrap_or(false)" in lib
    assert "identity.installer_permission===true" in ui
    assert "Android nie udzielił aplikacji uprawnienia" in ui
    # Neither permission nor a valid APK identity can unlock installation.
    assert "may_install_update(VerifiedTaskState::Unknown, false, false)" in lib
    assert "session.commit" not in bridge


def test_native_coordinator_reservation_reply_is_not_install_authorization():
    source = (ROOT / "remote_worker_gui/src-tauri/src/android_update_coordinator.rs").read_text(encoding="utf-8")
    for required in (
        "fn verify_reservation_reply(", "fn verify_release_reply(",
        'response.get("hold_token")', 'response.get("expires_at_unix_ms")',
        'response.get("authenticated")', 'response.get("source")',
        'response.get("worker_id")', 'response.get("nonce")',
        'response.get("install_allowed")', 'Some("reserved")',
        'Some("unknown")', "remaining < 5_000 || remaining > 305_000",
        "fn reservation_requires_bound_nonce_and_bounded_lifetime()",
        "fn release_is_never_conflated_with_idle_or_install_permission()",
    ):
        assert required in source
    lib = LIB.read_text(encoding="utf-8")
    # These parsers are private and still cannot enable Tauri installation.
    assert "may_install_update(VerifiedTaskState::Unknown, false, false)" in lib
    assert "reserve_worker_for_install" not in lib


def test_coordinator_reservation_transport_is_native_and_not_exposed_to_webview():
    lib = LIB.read_text(encoding="utf-8")
    coordinator = (ROOT / "remote_worker_gui/src-tauri/src/android_update_coordinator.rs").read_text(encoding="utf-8")
    ui = (ROOT / "remote_worker_gui/public/index.html").read_text(encoding="utf-8")
    for api in ("update-reserve", "update-release"):
        assert f'coordinator_endpoint(base, "{api}")?' in coordinator
    assert 'reqwest::redirect::Policy::none()' in coordinator
    assert "verify_reservation_reply(&body, worker_id, &nonce, system_unix_ms()?)" in coordinator
    assert "verify_release_reply(&body, worker_id, &nonce)" in coordinator
    assert '"hold_token": pending.hold_token' in coordinator
    assert 'save_pending(&pending)?' in coordinator
    assert 'load_pending()?' in coordinator
    assert 'clear_pending(&pending)' in coordinator
    assert 'pub(crate) async fn reserve_update(' in coordinator
    assert 'pub(crate) async fn release_update(' in coordinator
    assert 'fn authenticated_update_transport_rejects_untrusted_destinations()' in coordinator
    # Secret capabilities are never passed back to JavaScript. Until native
    # installer + cleanup are complete, no reserve button is allowed.
    assert "reserve_worker_update" not in lib
    assert "release_worker_update" not in lib
    assert "hold_token" not in ui
    assert "update_installation_readiness() -> UpdateReadiness" in lib
    assert "may_install_update(VerifiedTaskState::Unknown, false, false)" in lib


def test_update_hold_is_journaled_to_native_keyring_before_http_reserve():
    lib = LIB.read_text(encoding="utf-8")
    coordinator = (ROOT / "remote_worker_gui/src-tauri/src/android_update_coordinator.rs").read_text(encoding="utf-8")
    recovery = (ROOT / "remote_worker_gui/src-tauri/src/android_update_recovery.rs").read_text(encoding="utf-8")
    server = (ROOT / "server.py").read_text(encoding="utf-8")
    assert "mod android_update_recovery;" in lib
    assert 'const HOLD_ACCOUNT: &str = "coordinator-update-hold-v1"' in recovery
    assert 'keyring::Entry::new(HOLD_SERVICE, HOLD_ACCOUNT)' in recovery
    assert 'entry()?.set_password(&serialized)' in recovery
    assert "load_pending()?.is_some()" in recovery
    assert "load_pending()?.as_ref() != Some(record)" in recovery
    assert "load_pending()?.as_ref() != Some(expected)" in recovery
    assert "entry()?.delete_credential()" in recovery
    before = coordinator.index('save_pending(&pending)?')
    network = coordinator.index('let response = update_http_client()?.post(endpoint)', before)
    assert before < network
    assert '"hold_token": pending.hold_token' in coordinator
    assert "verified.token != pending.hold_token" in coordinator
    assert "hold_token=proposed_token" in server
    assert "install_allowed': False" in server
    # Neither the journal contents nor the signing key are in the webview.
    assert "hold_token" not in (ROOT / "remote_worker_gui/public/index.html").read_text(encoding="utf-8")


def test_update_recovery_status_never_exposes_hold_and_prevents_token_rotation():
    lib = LIB.read_text(encoding="utf-8")
    ui = (ROOT / "remote_worker_gui/public/index.html").read_text(encoding="utf-8")
    assert "fn update_recovery_status() -> UpdateRecoveryStatus" in lib
    assert "struct UpdateRecoveryStatus" in lib
    assert "pending: bool" in lib
    assert "blocked: bool" in lib
    registered = lib.split("tauri::generate_handler![", 1)[1].split("]", 1)[0]
    for command in ("update_recovery_status", "inspect_update_hold", "update_installation_readiness"):
        assert command in registered
    guard = lib.split("fn ensure_no_pending_update()", 1)[1].split("fn token_entry()", 1)[0]
    assert "android_update_recovery::load_pending()" in guard
    assert "Ok(Some(_)) => Err(" in guard
    assert "Err(_) => Err(" in guard
    assert "hold_token" not in guard
    assert "fn store_worker_token(token: String) -> Result<(), String> {\n    ensure_no_pending_update()?;" in lib
    assert "fn forget_worker_token() -> Result<(), String> {\n    ensure_no_pending_update()?;" in lib
    status = lib.split("fn update_recovery_status()", 1)[1].split("fn token_entry()", 1)[0]
    assert "Ok(Some(_)) => UpdateRecoveryStatus" in status
    assert "Err(_) => UpdateRecoveryStatus" in status
    assert "blocked: true" in status
    assert "hold_token" not in status
    assert 'id="updateRecoveryStatus"' in ui
    assert "invoke('update_recovery_status')" in ui
    assert "if(result?.blocked!==false)" in ui
    assert "document.getElementById('saveToken').disabled=true" in ui
    assert "document.getElementById('forgetToken').disabled=true" in ui
    assert "hold_token" not in ui
    assert "may_install_update(VerifiedTaskState::Unknown, false, false)" in lib


def test_hold_reconciliation_is_read_only_and_never_exposes_secret_to_ui():
    lib = LIB.read_text(encoding="utf-8")
    coordinator = (ROOT / "remote_worker_gui/src-tauri/src/android_update_coordinator.rs").read_text(encoding="utf-8")
    ui = (ROOT / "remote_worker_gui/public/index.html").read_text(encoding="utf-8")
    jobs = (ROOT / "remote_worker_jobs.py").read_text(encoding="utf-8")
    server = (ROOT / "server.py").read_text(encoding="utf-8")
    assert 'def inspect_update_hold(self, worker_id, token):' in jobs
    assert "'active' if self.clock() < row[1] else 'expired'" in jobs
    assert "secrets.compare_digest(row[0], token)" in jobs
    assert "update-hold-status" in server
    assert "registry.inspect_update_hold(worker_id, hold_token)" in server
    assert "'install_allowed': False" in server
    assert 'pub(crate) async fn inspect_pending_update(' in coordinator
    assert 'coordinator_endpoint(&pending.server, "update-hold-status")?' in coordinator
    assert 'load_pending()?' in coordinator
    assert 'match body.get("state").and_then(Value::as_str)' in coordinator
    assert 'async fn inspect_update_hold() -> Result<String, String>' in lib
    assert "inspect_update_hold" in lib.split("tauri::generate_handler![", 1)[1]
    assert "invoke('inspect_update_hold')" in ui
    assert "Rezerwacja wygasła, ale nadal blokuje przydziały" in ui
    assert "hold_token" not in ui
    assert "may_install_update(VerifiedTaskState::Unknown, false, false)" in lib


def test_android_package_installer_lifecycle_cannot_skip_user_consent_or_hold():
    root = ROOT / "remote_worker_gui/src-tauri/src"
    policy = (root / "android_update_install_state.rs").read_text(encoding="utf-8")
    lib = (root / "lib.rs").read_text(encoding="utf-8")
    ui = (ROOT / "remote_worker_gui/public/index.html").read_text(encoding="utf-8")
    assert "mod android_update_install_state;" in lib
    for required in (
        "CoordinatorReserved", "SessionCreated", "SessionCommitted",
        "AwaitingUserConsent", "ResultUnknown", "InstalledConfirmed",
        "CancelledConfirmed", "FailedConfirmed",
        "OsInstalledVersionAndSigningVerified",
        "OsCancelledSessionVerified", "OsFailedSessionVerified",
        "RestartWithoutConclusiveEvidence",
        "coordinator_hold_valid", "sha256_matches",
        "android_signature_matches", "version_increased", "os_permission",
        "fn interrupted_installer_does_not_allow_hold_release_or_retry()",
        "fn forged_and_out_of_order_events_cannot_skip_reservation()",
    ):
        assert required in policy
    assert "may_request_hold_release(unknown)" in policy
    assert 'may_install_update(VerifiedTaskState::Unknown, false, false)' in lib
    assert "install_worker_apk" not in lib
    assert "install_worker_apk" not in ui


def test_package_installer_own_sessions_are_read_only_and_never_confirm_install():
    source = (ROOT / "remote_worker_gui/src-tauri/src/android_update_package_installer.rs").read_text(encoding="utf-8")
    lib = LIB.read_text(encoding="utf-8")
    ui = (ROOT / "remote_worker_gui/public/index.html").read_text(encoding="utf-8")
    assert '#[cfg(target_os = "android")]\nmod android_update_package_installer;' in lib
    for method in ('getPackageInstaller', 'getMySessions', 'getInstallerPackageName',
                   'getAppPackageName', 'getSessionId', 'isCommitted'):
        assert f'"{method}"' in source
    assert 'if count < 0 || count > 1' in source
    assert 'if count == 0 { return Ok(SessionObservation::NoVisibleSession); }' in source
    assert 'target.as_deref() != Some(WORKER_PACKAGE_ID)' in source
    assert 'owner.as_deref() != Some(WORKER_PACKAGE_ID)' in source
    assert 'fn observation_is_terminal(_observation: SessionObservation) -> bool' in source
    assert 'fn inspect_android_install_sessions() -> Result<&\'static str, String>' in lib
    assert 'android_update_recovery::load_pending()?.is_none()' in lib
    assert 'inspect_android_install_sessions' in lib.split('tauri::generate_handler![', 1)[1]
    assert 'update_installation_readiness' in lib.split('tauri::generate_handler![', 1)[1]
    assert "invoke('inspect_android_install_sessions')" in ui
    assert "Brak widocznej sesji: to NIE potwierdza zakończenia instalacji" in ui
    executable = '\n'.join(line for line in source.splitlines() if not line.lstrip().startswith('//'))
    for unsafe_action in ('createSession', 'openSession', 'abandonSession', 'commit(', 'startActivity'):
        assert unsafe_action not in executable
    assert 'may_install_update(VerifiedTaskState::Unknown, false, false)' in lib


def test_authenticated_stop_poll_is_wired_to_tauri_but_cannot_ack_or_install():
    rust = LIB.read_text(encoding="utf-8")
    coordinator = (ROOT / "remote_worker_gui/src-tauri/src/android_update_coordinator.rs").read_text(encoding="utf-8")
    ui = (ROOT / "remote_worker_gui/public/index.html").read_text(encoding="utf-8")
    assert 'async fn poll_worker_update_pause(server: String, worker_id: String)' in rust
    assert 'android_update_recovery::load_pending()?.is_some()' in rust
    assert 'android_update_coordinator::poll_pause_instruction(' in rust
    assert 'StopInstruction::StopCheckpoint { .. } => Ok("stop_checkpoint")' in rust
    assert 'poll_worker_update_pause, update_installation_readiness' in rust
    assert 'coordinator_endpoint(base, "update-pause-poll")?' in coordinator
    assert 'verify_pause_poll_reply(&body, worker_id, &nonce)' in coordinator
    assert "invoke('poll_worker_update_pause'" in ui
    assert 'STOP nie został wykonany.' in ui
    assert 'Brak STOP/ACK; instalacja zablokowana.' in ui
    assert 'may_install_update(VerifiedTaskState::Unknown, false, false)' in rust
    assert 'install_worker_apk' not in rust
