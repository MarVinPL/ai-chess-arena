"""Regression checks for the explicit manual APK update fallback.

This is a source-level wiring test, not a claim of Android OS install success.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUI = (ROOT / "remote_worker_gui/public/index.html").read_text(encoding="utf-8")
RUST = (ROOT / "remote_worker_gui/src-tauri/src/lib.rs").read_text(encoding="utf-8")


def test_manual_release_link_is_explicit_and_opens_outside_webview():
    assert 'id="manualWorkerUpdate"' in GUI
    assert 'target="_blank" rel="noopener noreferrer"' in GUI
    assert "manual.hidden=true;manual.style.display='none';manual.removeAttribute('href')" in GUI


def test_manual_link_only_on_validated_new_worker_apk():
    assert "channel==='latest' && info.update_available===true && info.apk_asset_available===true" in GUI
    assert "url==='https://github.com/MarVinPL/ai-chess-arena/releases/tag/'+tag" in GUI
    assert "info.release_notes_url" in GUI
    assert "manual.href=info.release_notes_url" in GUI
    assert "manual.hidden=false;manual.style.display='inline-block'" in GUI
    assert "github\\.com\\/MarVinPL\\/ai-chess-arena\\/releases\\/tag\\/worker-v" in GUI
    assert 'url.host_str() == Some("github.com")' in RUST
    assert 'url.path() == expected_path' in RUST


def test_fallback_does_not_misrepresent_native_install():
    assert "Instalacja pozostaje wyłączona." in GUI
    assert "fn update_installation_readiness()" in RUST
    assert "VerifiedTaskState::Unknown, false, false" in RUST
