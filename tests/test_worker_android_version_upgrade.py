"""Signed test APK must have an installable version above installed 0.1.2."""
import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "remote_worker_gui"


def test_worker_app_version_0_1_3_is_consistent_in_source_and_locks():
    tauri = json.loads((ROOT / "src-tauri/tauri.conf.json").read_text())
    package = json.loads((ROOT / "package.json").read_text())
    lock = json.loads((ROOT / "package-lock.json").read_text())
    manifest = tomllib.loads((ROOT / "src-tauri/Cargo.toml").read_text())
    cargo_lock = tomllib.loads((ROOT / "src-tauri/Cargo.lock").read_text())
    assert tauri["version"] == package["version"] == "0.1.3"
    assert lock["version"] == lock["packages"][""]["version"] == "0.1.3"
    assert manifest["package"]["version"] == "0.1.3"
    packages = [row for row in cargo_lock["package"] if row["name"] == "ai-chess-arena-worker"]
    assert len(packages) == 1 and packages[0]["version"] == "0.1.3"


def test_staged_update_requires_higher_android_version_code():
    gate = (ROOT / "src-tauri/src/android_update_identity.rs").read_text()
    assert "candidate.long_version_code <= installed.long_version_code" in gate
    # Real Android versionCode is verified with aapt after CI builds the APK.
