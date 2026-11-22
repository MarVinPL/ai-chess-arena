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
