"""Regression checks for versioned Android APK filenames in the Worker workflow."""
from pathlib import Path

WORKFLOW = (
    Path(__file__).resolve().parents[1]
    / ".github/workflows/android-worker-apk.yml"
).read_text(encoding="utf-8")


def test_version_is_read_from_compiled_android_apk():
    assert '"$ANDROID_HOME/build-tools/36.0.0/aapt" dump badging "$apk"' in WORKFLOW
    assert "versionName=" in WORKFLOW
    assert '[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]' in WORKFLOW


def test_filename_is_versioned_and_marked_pre_not_test():
    assert 'name="ai-chess-arena-worker-v${version}-pre${GITHUB_RUN_NUMBER}-arm64.apk"' in WORKFLOW
    assert 'cp "$apk" "dist/$name"' in WORKFLOW
    assert 'sha256sum "$name" > SHA256SUMS' in WORKFLOW
    assert "sha256sum --check SHA256SUMS" in WORKFLOW
    assert 'dist/ai-chess-arena-worker-arm64-debug.apk' not in WORKFLOW


def test_original_signing_secret_and_historical_artifact_reference_unchanged():
    assert "WORKER_ANDROID_DEBUG_KEYSTORE_B64" in WORKFLOW
    assert "3f0449ec3a2399ec669cce2dcbd6aa7262671345dfb983c33e2cadf4d1b0da12" in WORKFLOW
    # Stable workflow artifact identity avoids breaking existing pruning and pinned runs.
    assert "name: ai-chess-arena-worker-arm64-debug" in WORKFLOW
