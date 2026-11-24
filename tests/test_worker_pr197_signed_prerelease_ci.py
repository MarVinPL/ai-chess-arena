"""One-shot original-key Android test release: build and signing stay isolated."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WF = (ROOT / ".github/workflows/worker-android-pr197-original-key-prerelease.yml").read_text()
PR_SHA = "8a111b04aaaee2e4dbf78c35443bae723a1e8d5d"
ORIGINAL_CERT = "3f0449ec3a2399ec669cce2dcbd6aa7262671345dfb983c33e2cadf4d1b0da12"


def sections():
    return WF.split("\n  build:", 1)[1].split("\n  sign:", 1)[0], (
        WF.split("\n  sign:", 1)[1].split("\n  publish:", 1)[0]
    ), WF.split("\n  publish:", 1)[1]


def test_only_manual_trusted_main_can_start_test_release():
    assert "workflow_dispatch:" in WF
    assert "\n  pull_request_target:" not in WF
    assert "  pull_request:" not in WF
    assert "  push:" not in WF
    assert WF.count("github.ref == 'refs/heads/main'") == 3
    assert "cancel-in-progress: false" in WF


def test_unmerged_source_is_pinned_and_compiled_without_secrets():
    build, sign, publish = sections()
    assert build.count(PR_SHA) >= 2
    assert "pulls/197 --jq '.head.sha'" in build
    assert "head.repo.full_name" in build
    assert "persist-credentials: false" in build
    assert "actions/checkout@v6" in build
    assert "npx tauri android build --apk --target aarch64 --ci --debug" in build
    assert "python3 android/inject_package_installer.py" in build
    assert "secrets." not in build
    assert "KEYSTORE_B64" not in build
    assert "worker-pr197-011-untrusted-build" in build
    assert "1001' versionName='0.1.1" in build
    assert "native-code: 'arm64-v8a'" in build


def test_signer_has_no_source_checkout_and_validates_original_cert():
    build, sign, publish = sections()
    assert "actions/checkout" not in sign
    assert "npx " not in sign
    assert "npm " not in sign
    assert "actions/download-artifact@v4" in sign
    assert "input/SHA256SUMS" in sign
    assert "sha256sum --check SHA256SUMS" in sign
    assert "WORKER_ANDROID_DEBUG_KEYSTORE_B64" in sign
    assert "WORKER_ANDROID_DEBUG_STORE_PASSWORD" in sign
    assert "WORKER_ANDROID_DEBUG_KEY_PASSWORD" in sign
    assert sign.count(ORIGINAL_CERT) >= 2
    assert '$ANDROID_HOME/build-tools/36.0.0/apksigner" sign' in sign
    assert '"$tools/apksigner" verify --verbose' in sign
    assert "versionCode='1001' versionName='0.1.1'" in sign
    assert "worker-pr197-011-original-key-verified" in sign


def test_publisher_cannot_use_signing_key_or_republish_existing_release():
    _, sign, publish = sections()
    assert "actions/checkout" not in publish
    assert "secrets." not in publish
    assert "KEYSTORE" not in publish
    assert "contents: write" in publish
    assert "gh release view" in publish
    assert "gh release create" in publish
    assert "--prerelease --latest=false" in publish
    assert PR_SHA in publish
    assert "worker-v0.1.1-test.3" in publish
    assert "SHA256SUMS" in publish and "PROVENANCE.txt" in publish
    assert "versionCode 1001" in publish
