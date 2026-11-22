"""The historical-key Android APK test release must never expose signing secrets."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/worker-android-original-key-test-release.yml"
DOCS = ROOT / "docs/worker-android-original-key-test-release.md"
SELECTIVE = ROOT / ".github/workflows/worker-multiplatform.yml"
CERT = "3f0449ec3a2399ec669cce2dcbd6aa7262671345dfb983c33e2cadf4d1b0da12"
SOURCE = "fb1a9df1909fbb9f91bf3b881bc798fef98185848b1a04534ffed27bf96d16fe"
COMMIT = "2c8e6660202e8cab032e7533c2b1c9ed507d6a47"


def test_manual_main_only_trusted_signing_and_permissions():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "on:\n  workflow_dispatch:" in text
    assert "    if: github.event_name == 'workflow_dispatch' && github.ref == 'refs/heads/main'" in text
    assert "permissions: {}" in text
    signed = text.split("\n  sign:\n", 1)[1].split("\n  publish:\n", 1)[0]
    publish = text.split("\n  publish:\n", 1)[1]
    assert "contents: read" in signed and "actions: read" in signed
    assert "contents: write" not in signed
    assert "contents: write" in publish
    assert "secrets.WORKER_ANDROID_DEBUG" not in publish
    assert "secrets: inherit" not in text
    assert signed.count("uses: actions/download-artifact@v4") == 1
    assert "run-id: 45018325702" in signed
    assert "repository: ${{ github.repository }}" in signed
    assert "github-token: ${{ github.token }}" in signed
    assert "name: ai-chess-arena-worker-arm64-debug" in signed
    assert "path: input" in signed
    assert publish.count("uses: actions/download-artifact@v4") == 1
    assert "name: worker-v0.1.0-test1-original-key-verified" in publish
    assert "path: dist" in publish
    assert "run-id:" not in publish
    assert "github-token:" not in publish
    assert "gh run download" not in text
    assert "actions/upload-artifact@v4" in signed
    assert "npx tauri android build" not in text
    assert "gradle" not in text.lower()
    assert "WORKER_ANDROID_DEBUG_KEYSTORE_B64" in signed
    assert "WORKER_ANDROID_DEBUG_STORE_PASSWORD" in signed
    assert "WORKER_ANDROID_DEBUG_KEY_PASSWORD" in signed
    assert "github.event.pull_request" not in signed


def test_pinned_provenance_and_verify_before_upload():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "run-id: 45018325702" in text
    assert COMMIT in text
    assert SOURCE in text
    assert CERT in text
    assert "sha256sum -c SHA256SUMS" in text
    assert "versionCode='1000' versionName='0.1.0'" in text
    assert "native-code: 'arm64-v8a'" in text
    assert '"$tools/apksigner" verify --verbose "$apk"' in text
    assert '"$tools/zipalign" -c -p 4 "$apk"' in text
    assert "trap 'rm -f \"$ks\"' EXIT" in text
    assert text.index('test "$certificate" = ' + CERT) < text.index('actions/upload-artifact@v4')
    assert "worker-v0.1.0-test.1" in text
    assert "--target " + COMMIT in text
    assert "--prerelease --latest=false" in text


def test_no_desktop_build_reactivation_or_faux_production_release():
    selective = SELECTIVE.read_text(encoding="utf-8")
    assert "false && needs.detect.outputs.linux == 'true'" in selective
    assert "false && needs.detect.outputs.windows == 'true'" in selective
    docs = DOCS.read_text(encoding="utf-8")
    assert "debug-key compatibility only" in docs
    assert "not" in docs.lower()
    assert "main" in docs
    assert "token" in docs.lower()
    assert "worker-v0.1.0-test.1" in docs
