"""Issue #249: Android Worker uses the official CLI, not deprecated sdkmanager."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/android-worker-apk.yml"
SMOKE = ROOT / ".github/workflows/android-cli-smoke.yml"


def test_android_sdk_uses_new_cli_without_sdkmanager_invocations():
    text = WORKFLOW.read_text()
    assert "android-actions/setup-android@v4" in text
    assert "cmdline-tools-version: '15859902'" in text
    assert "accept-android-sdk-licenses: 'false'" in text
    assert "packages: ''" in text
    assert 'android --sdk="$ANDROID_HOME" sdk install platform-tools platforms/android-36 build-tools/36.0.0 ndk/27.2.12479018' in text
    assert "yes | sdkmanager" not in text
    assert "sdkmanager --licenses" not in [
        line.strip() for line in text.splitlines()
        if not line.strip().startswith("#")
    ]
    assert "npx tauri android init --ci --skip-targets-install" in text


def test_sdk_workflow_changes_build_unsigned_apk_on_pr_without_secrets():
    smoke = SMOKE.read_text()
    assert "pull_request:" in smoke
    assert "'.github/workflows/android-worker-apk.yml'" in smoke
    assert "uses: ./.github/workflows/android-worker-apk.yml" in smoke
    assert "secrets:" not in smoke
    workflow = WORKFLOW.read_text()
    assert "if: github.event_name == 'push' && github.ref == 'refs/heads/main'" in workflow
    assert "WORKER_ANDROID_DEBUG_KEYSTORE_B64" in workflow
    assert "npx tauri android build --apk --target aarch64 --ci --debug" in workflow
