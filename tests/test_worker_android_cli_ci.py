"""Guard the Android Worker CI migration to Google's version-pinned Android CLI."""
from pathlib import Path


def test_android_worker_uses_verified_android_cli_without_sdkmanager():
    workflow = (Path(__file__).resolve().parents[1] /
                '.github/workflows/android-worker-apk.yml').read_text()
    assert 'android-actions/setup-android@' not in workflow
    assert 'sdkmanager' not in workflow
    assert 'commandlinetools-linux-15859902_latest.zip' in workflow
    assert '4e4c464f145a7512b57d088ac6c278c03c9eea610886b35a5e0804e74eedf583' in workflow
    assert 'sha256sum --check -' in workflow
    assert '--sdk="$ANDROID_HOME" sdk install' in workflow
    for package in ('platform-tools', 'platforms/android-36', 'build-tools/36.0.0',
                    'ndk/27.2.12479018'):
        assert package in workflow
    assert 'test -x "$ANDROID_HOME/build-tools/36.0.0/apksigner"' in workflow
    assert 'targets: aarch64-linux-android' in workflow
    assert 'npx tauri android init --ci --skip-targets-install' in workflow
    assert 'python3 ../scripts/retry_http_429.py npx --no-install tauri android build --apk --target aarch64 --ci --debug' in workflow
    assert 'WORKER_ANDROID_DEBUG_KEYSTORE_B64' in workflow
