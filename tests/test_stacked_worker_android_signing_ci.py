"""Prevent signing secret exposure while synchronizing Worker CI into main."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CALLER = ROOT / ".github/workflows/worker-multiplatform.yml"
CALLED = ROOT / ".github/workflows/android-worker-apk.yml"
FINGERPRINT = "3f0449ec3a2399ec669cce2dcbd6aa7262671345dfb983c33e2cadf4d1b0da12"


def test_only_main_android_build_receives_explicit_secrets():
    text = CALLER.read_text(encoding="utf-8")
    pr = text.split("  android:\n", 1)[1].split("  android_signed:\n", 1)[0]
    main = text.split("  android_signed:\n", 1)[1]
    assert "github.event_name == 'pull_request'" in pr
    assert "secrets:" not in pr
    assert "github.event_name == 'push'" in main
    assert "github.ref == 'refs/heads/main'" in main
    assert "secrets: inherit" not in text
    for suffix in ("KEYSTORE_B64", "STORE_PASSWORD", "KEY_PASSWORD"):
        assert "secrets.WORKER_ANDROID_DEBUG_" + suffix in main
    assert "false && (needs.changes.outputs.linux" in text


def test_keystore_reused_after_icon_generation_and_before_build():
    text = CALLED.read_text(encoding="utf-8")
    assert text.index("npx tauri android init --ci") < text.index(
        "npx tauri icon src-tauri/icons/icon.png"
    ) < text.index("Restore historical debug signing key") < text.index(
        "npx tauri android build --apk"
    )
    assert "if: github.event_name == 'push' && github.ref == 'refs/heads/main'" in text
    assert 'chmod 600 "$HOME/.android/debug.keystore"' in text
    assert "-storepass:env STORE_PASSWORD" in text
    assert 'test "$actual" = ' + FINGERPRINT in text
    assert 'test "$signed_cert" = ' + FINGERPRINT in text
    assert text.index('test "$signed_cert" = ' + FINGERPRINT) < text.index("actions/upload-artifact@v6")
    assert "secrets: inherit" not in text
