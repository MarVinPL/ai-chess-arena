"""Regression guard: Android ARM64 CI must not reinstall unused Rust targets."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_android_workflow_reuses_pinned_arm64_target():
    workflow = (Path(__file__).resolve().parents[1] /
                '.github/workflows/android-worker-apk.yml').read_text()
    assert 'rustup toolchain install stable --profile minimal --target aarch64-linux-android' in workflow
    assert 'npx tauri android init --ci --skip-targets-install' in workflow
    assert 'python3 ../scripts/retry_http_429.py npx --no-install tauri android build --apk --target aarch64 --ci --debug' in workflow
    assert 'npx tauri android init --ci\n' not in workflow
    assert 'aarch64-linux-android' in workflow
    assert 'WORKER_ANDROID_DEBUG_KEYSTORE_B64' in workflow


def test_gradle_distribution_has_its_own_cache_key_before_tauri_init():
    workflow = (ROOT / '.github/workflows/android-worker-apk.yml').read_text()
    key_path = '.github/gradle-cache/gradle-wrapper.properties'
    key = (ROOT / key_path).read_text()
    setup = workflow.split('name: Restore independent Gradle wrapper cache', 1)[1].split(
        'name: Install pinned official Android CLI', 1
    )[0]
    assert 'uses: actions/cache/restore@caa296126883cff596d87d8935842f9db880ef25' in setup
    assert 'path: /home/runner/.gradle/wrapper' in setup
    assert f"hashFiles('{key_path}')" in setup
    assert 'gradle-9.6.1-bin.zip' in key
    assert 'distributionPath=wrapper/dists' in key
    assert 'cache-directories: ~/.gradle/wrapper/dists' not in workflow
    assert 'The generated Gradle wrapper version differs from the cache key.' in workflow
