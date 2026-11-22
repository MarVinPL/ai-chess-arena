"""Ensure Android Worker CI asks for a published stable SDK platform."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_android_sdk_installer_uses_available_api_36_platform():
    workflow = (ROOT / ".github/workflows/android-worker-apk.yml").read_text(encoding="utf-8")
    installer = (ROOT / "scripts/install_android_sdk_cli.sh").read_text(encoding="utf-8")
    assert 'run: bash ../scripts/install_android_sdk_cli.sh' in workflow
    assert 'android --sdk="$ANDROID_HOME" sdk install' in installer
    assert 'platforms/android-36' in installer
    assert 'build-tools/36.0.0' in installer
    assert 'ndk/27.2.12479018' in installer
    assert 'test -s "$ANDROID_HOME/platforms/android-36/android.jar"' in installer
    assert 'test -d "$ANDROID_HOME/ndk/27.2.12479018"' in installer
    assert 'platforms/android-37' not in installer


def test_workflow_only_changes_do_not_trigger_builds():
    parent = (ROOT / ".github/workflows/worker-multiplatform.yml").read_text(encoding="utf-8")
    assert "workflow_dispatch:" not in parent
    assert "    paths:" in parent
    # Reusable-workflow references are valid. Only paths-filter triggers
    # could build unchanged code; a mere `uses:` line is not a trigger.
    assert "      - '.github/workflows/android-worker-apk.yml'" not in parent
    assert "      - '.github/workflows/worker-multiplatform.yml'" not in parent
