"""Issue #249: Android Worker uses the official CLI, not deprecated sdkmanager."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/android-worker-apk.yml"
SMOKE = ROOT / ".github/workflows/android-cli-smoke.yml"
INSTALL = ROOT / "scripts/install_android_sdk_cli.sh"


def test_android_sdk_uses_new_cli_without_sdkmanager_invocations():
    workflow = WORKFLOW.read_text()
    script = INSTALL.read_text()
    assert "android-actions/setup-android@v4" in workflow
    assert "cmdline-tools-version: '15859902'" in workflow
    assert "accept-android-sdk-licenses: 'false'" in workflow
    assert "packages: ''" in workflow
    assert "run: bash ../scripts/install_android_sdk_cli.sh" in workflow
    assert 'android --sdk="$ANDROID_HOME" sdk install' in script
    assert "platform-tools" in script
    assert "platforms/android-36" in script
    assert "build-tools/36.0.0" in script
    assert "ndk/27.2.12479018" in script
    # No executable call to deprecated sdkmanager in either script or workflow.
    for text in (workflow, script):
        for line in text.splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                assert not stripped.startswith(("sdkmanager ", "yes | sdkmanager"))
    assert "npx tauri android init --ci --skip-targets-install" in workflow


def test_sdk_smoke_on_pr_works_without_unmerged_android_worker():
    smoke = SMOKE.read_text()
    assert "pull_request:" in smoke
    assert "'.github/workflows/android-worker-apk.yml'" in smoke
    assert "scripts/install_android_sdk_cli.sh" in smoke
    assert "run: bash scripts/install_android_sdk_cli.sh" in smoke
    assert "uses: ./.github/workflows/android-worker-apk.yml" not in smoke
    assert "accept-android-sdk-licenses: 'false'" in smoke
    assert "packages: ''" in smoke
    assert "secrets:" not in smoke


def test_signed_apk_build_still_unmodified():
    workflow = WORKFLOW.read_text()
    assert "if: github.event_name == 'push' && github.ref == 'refs/heads/main'" in workflow
    assert "WORKER_ANDROID_DEBUG_KEYSTORE_B64" in workflow
    assert "npx tauri android build --apk --target aarch64 --ci --debug" in workflow
