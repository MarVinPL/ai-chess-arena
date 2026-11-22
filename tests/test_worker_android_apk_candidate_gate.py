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
    assert "bounded GitHub metadata" in release_command
    assert "MAX_RELEASE_JSON: usize = 1024 * 1024" in release_command
    assert "if chunk.len() > MAX_RELEASE_JSON - body.len()" in release_command
    assert "serde_json::from_slice(&body)" in release_command
    assert "info.apk_asset_available===true" in ui
    assert "Brak kwalifikującego się pliku APK ARM64" in ui
    assert "Pobieranie i instalacja nie są jeszcze dostępne." in ui
    assert "invoke('check_worker_release')" in ui
    assert "invoke('download_update')" not in ui
    assert "invoke('install_update')" not in ui
