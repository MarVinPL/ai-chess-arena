"""Regression checks: historical Android signing key must be main-only."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CALLER = ROOT / ".github/workflows/worker-multiplatform.yml"
CALLED = ROOT / ".github/workflows/android-worker-apk.yml"

FINGERPRINT = "3f0449ec3a2399ec669cce2dcbd6aa7262671345dfb983c33e2cadf4d1b0da12"


def test_untrusted_pr_never_passes_signing_secrets():
    workflow = CALLER.read_text(encoding="utf-8")
    pr_job = workflow.split("  android:\n", 1)[1].split("  android_signed:\n", 1)[0]
    signed_job = workflow.split("  android_signed:\n", 1)[1].split("  prune:\n", 1)[0]

    assert "github.event_name == 'pull_request'" in pr_job
    assert "secrets:" not in pr_job
    assert "WORKER_ANDROID_DEBUG_KEYSTORE_B64" not in pr_job
    assert "secrets: inherit" not in workflow
    assert "github.event_name == 'push'" in signed_job
    assert "github.ref == 'refs/heads/main'" in signed_job
    for name in (
        "WORKER_ANDROID_DEBUG_KEYSTORE_B64",
        "WORKER_ANDROID_DEBUG_STORE_PASSWORD",
        "WORKER_ANDROID_DEBUG_KEY_PASSWORD",
    ):
        assert "secrets." + name in signed_job


def test_main_build_restores_keystore_only_after_android_init():
    workflow = CALLED.read_text(encoding="utf-8")
    assert workflow.index("npx tauri android init --ci") < workflow.index(
        "Restore original Android test signing key"
    ) < workflow.index("npx tauri android build --apk")
    assert "if: github.event_name == 'push' && github.ref == 'refs/heads/main'" in workflow
    assert 'printf \'%s\' "$WORKER_KEYSTORE_B64" | base64 --decode > "$HOME/.android/debug.keystore"' in workflow
    assert 'chmod 600 "$HOME/.android/debug.keystore"' in workflow
    assert "-storepass:env STORE_PASSWORD" in workflow
    assert FINGERPRINT in workflow
    assert "secrets: inherit" not in workflow


def test_apk_certificate_checked_before_upload():
    workflow = CALLED.read_text(encoding="utf-8")
    assert workflow.index('"$signer" verify --verbose "$apk"') < workflow.index(
        'test "$signed_cert" = ' + FINGERPRINT
    ) < workflow.index("actions/upload-artifact@v6")
    assert "if: github.event_name == 'push' && github.ref == 'refs/heads/main'" in workflow
    assert "if-no-files-found: error" in workflow
    assert "workflow_dispatch:" in workflow  # only in a comment, not a trigger
    assert not any(line.strip() == "workflow_dispatch:" for line in workflow.splitlines())


def test_android_only_no_irrelevant_rebuilds():
    caller = CALLER.read_text(encoding="utf-8")
    assert "false && needs.detect.outputs.linux == 'true'" in caller
    assert "false && needs.detect.outputs.windows == 'true'" in caller
    assert "    paths:" in caller
    assert not any(line.strip() == "workflow_dispatch:" for line in caller.splitlines())
