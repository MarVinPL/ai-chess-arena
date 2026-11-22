"""Ensure Android Worker CI installs stable SDK through the verified Android CLI."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_android_sdk_installer_uses_available_api_36_platform():
    workflow = (ROOT / ".github/workflows/android-worker-apk.yml").read_text(encoding="utf-8")
    assert "sdkmanager" not in workflow
    assert 'android="$ANDROID_HOME/cmdline-tools/22.0/bin/android"' in workflow
    assert 'platform-tools platforms/android-36 build-tools/36.0.0 ndk/27.2.12479018' in workflow
    assert 'sha256sum --check -' in workflow
    assert 'sdk install' in workflow

    assert 'test -s "$ANDROID_HOME/platforms/android-36/android.jar"' in workflow
    assert 'test -d "$ANDROID_HOME/ndk/27.2.12479018"' in workflow
    assert '"platforms;android-37"' not in workflow


def test_workflow_only_changes_do_not_trigger_builds():
    parent = (ROOT / ".github/workflows/worker-multiplatform.yml").read_text(encoding="utf-8")
    assert "workflow_dispatch:" not in parent
    assert "    paths:" in parent
    # Reusable-workflow references are valid. Only paths-filter triggers
    # could build unchanged code; a mere `uses:` line is not a trigger.
    assert "      - '.github/workflows/android-worker-apk.yml'" not in parent
    assert "      - '.github/workflows/worker-multiplatform.yml'" not in parent
