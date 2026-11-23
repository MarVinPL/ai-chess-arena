"""Regression checks for locating APK signature verifier in Android CI."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "android-worker-apk.yml"


def test_android_apksigner_comes_from_installed_sdk_build_tools():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert 'signer="$ANDROID_HOME/build-tools/36.0.0/apksigner"' in text
    assert 'test -x "$signer"' in text
    assert '"$signer" verify --verbose "$apk"' in text
    assert '          apksigner verify --verbose "$apk"' not in text


def test_apk_is_verified_before_artifact_upload():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert text.index('"$signer" verify --verbose "$apk"') < text.index("actions/upload-artifact@")
    assert 'if-no-files-found: error' in text
    assert not any(line.strip() == "workflow_dispatch:" for line in text.splitlines())


def test_workflow_only_edits_do_not_build_application():
    parent = (ROOT / ".github/workflows/worker-multiplatform.yml").read_text(encoding="utf-8")
    assert "workflow_dispatch:" not in parent
    assert "      - '.github/workflows/android-worker-apk.yml'" not in parent
    assert "      - '.github/workflows/worker-multiplatform.yml'" not in parent
