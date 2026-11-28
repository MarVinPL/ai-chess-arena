"""One-shot trusted Android test.2 release must isolate unmerged source builds."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/worker-android-channels-test2-release.yml"
ORIGINAL = ROOT / ".github/workflows/worker-android-original-key-test-release.yml"
SOURCE = "16d4a98bedf08f4f985caed4701e922fb99a9866"
CERT = "3f0449ec3a2399ec669cce2dcbd6aa7262671345dfb983c33e2cadf4d1b0da12"


def sections():
    s = WORKFLOW.read_text(encoding="utf-8")
    build = s.split("\n  build:\n", 1)[1].split("\n  sign:\n", 1)[0]
    sign = s.split("\n  sign:\n", 1)[1].split("\n  publish:\n", 1)[0]
    publish = s.split("\n  publish:\n", 1)[1]
    return s, build, sign, publish


def test_only_main_manual_dispatch_and_pinned_unmerged_worker():
    s, build, sign, publish = sections()
    assert "on:\n  workflow_dispatch:" in s
    assert "permissions: {}" in s
    assert s.count("github.ref == 'refs/heads/main'") == 3
    assert "pulls/193" in build
    assert build.count(SOURCE) >= 2
    assert f"ref: {SOURCE}" in build
    assert "persist-credentials: false" in build
    assert f"--target {SOURCE}" in publish
    assert "--prerelease --latest=false" in publish
    assert "worker-v0.1.0-test.2" in publish
    assert "worker-v0.1.0-test.1" not in publish
    assert "worker-v0.1.0-test.1" in ORIGINAL.read_text()


def test_untrusted_pr_source_compiles_without_signing_secrets():
    s, build, sign, publish = sections()
    assert "WORKER_ANDROID_DEBUG" not in build
    assert "secrets." not in build
    assert "contents: read" in build
    assert "contents: write" not in build
    assert "persist-credentials: false" in build
    assert "npx tauri android build --apk --target aarch64 --ci --debug" in build
    assert "actions/upload-artifact@v6" in build
    assert "actions/download-artifact@v4" in sign
    assert "actions/download-artifact@v4" in publish
    assert sign.index("name: Verify exact build artifact before restoring the keystore") < sign.index("name: Re-sign using isolated original debug key")
    assert "No signing" not in build  # no misleading success claim


def test_signer_is_isolated_and_publisher_never_receives_key():
    s, build, sign, publish = sections()
    assert "secrets.WORKER_ANDROID_DEBUG_KEYSTORE_B64" in sign
    assert "secrets.WORKER_ANDROID_DEBUG_STORE_PASSWORD" in sign
    assert "secrets.WORKER_ANDROID_DEBUG_KEY_PASSWORD" in sign
    assert "secrets.WORKER_ANDROID_DEBUG" not in publish
    assert "contents: write" not in sign
    assert "contents: write" in publish
    assert "needs: sign" in publish
    assert CERT in sign and CERT in publish
    assert '"$tools/apksigner" verify --verbose "$apk"' in sign
    assert '"$tools/zipalign" -c -p 4 "$apk"' in sign
    assert "versionCode='1000' versionName='0.1.0'" in sign
    assert "native-code: 'arm64-v8a'" in sign
    assert "gh release create" in publish
    assert "refusing to overwrite" in publish
    assert "PROVENANCE.txt" in sign and "SHA256SUMS" in publish


def test_is_a_manual_test_release_without_auto_installation_or_desktop_builds():
    s, build, sign, publish = sections()
    assert "workflow_dispatch" in s
    assert "on:\n  push:" not in s and "on:\n  pull_request:" not in s
    assert "npx tauri android build" in build
    assert "npx tauri android build" not in sign
    assert "npx tauri android build" not in publish
    assert "Auto install" not in s
    assert "rm -rf" not in s
    assert "latest=false" in publish
