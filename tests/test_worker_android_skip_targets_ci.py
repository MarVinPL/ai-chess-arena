"""Regression guard: Android ARM64 CI must not reinstall unused Rust targets."""
from pathlib import Path


def test_android_workflow_reuses_pinned_arm64_target():
    workflow = (Path(__file__).resolve().parents[1] /
                '.github/workflows/android-worker-apk.yml').read_text()
    assert 'targets: aarch64-linux-android' in workflow
    assert 'npx tauri android init --ci --skip-targets-install' in workflow
    assert 'python3 ../scripts/retry_http_429.py npx --no-install tauri android build --apk --target aarch64 --ci --debug' in workflow
    assert 'npx tauri android init --ci\n' not in workflow
    assert 'aarch64-linux-android' in workflow
    assert 'WORKER_ANDROID_DEBUG_KEYSTORE_B64' in workflow
