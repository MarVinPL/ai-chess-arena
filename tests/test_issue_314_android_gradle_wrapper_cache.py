"""Regression guard: cached Gradle wrapper for generated Tauri Android builds.

GitHub-hosted runners discard ~/.gradle between jobs. Npm and Rust caches do
not include Gradle wrapper/dists. Keep this free GitHub Actions cache before
the first Tauri Android Gradle invocation.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github/workflows/android-worker-apk.yml").read_text(
    encoding="utf-8"
)


def test_gradle_wrapper_distribution_is_cached_before_tauri_build():
    # Reuse the already approved Rust cache action: it supports extra folders.
    assert "uses: swatinem/rust-cache@v2" in WORKFLOW
    assert "cache-directories: ~/.gradle/wrapper/dists" in WORKFLOW
    assert "key: android-arm64-gradle-9.6.1" in WORKFLOW
    assert "uses: actions/cache@v4" not in WORKFLOW
    assert WORKFLOW.index("name: Restore Rust and Gradle wrapper caches") < WORKFLOW.index(
        "name: Initialize Android Worker"
    )
    assert WORKFLOW.index("name: Restore Rust and Gradle wrapper caches") < WORKFLOW.index(
        "name: Compile Android Worker"
    )


def test_cache_restoration_does_not_change_gradle_or_android_signing_policy():
    assert "npm ci" in WORKFLOW
    assert "npx tauri android init --ci --skip-targets-install" in WORKFLOW
    assert "npx tauri android build --apk --target aarch64 --ci --debug" in WORKFLOW
    assert "if: github.event_name == 'push' && github.ref == 'refs/heads/main'" in WORKFLOW
    assert "Restore original Android test signing key" in WORKFLOW
    assert "Verify signature and SHA-256" in WORKFLOW
    assert "GRADLE_OPTS:" in WORKFLOW


def test_cache_step_reports_hits_without_network_or_secret_operations():
    block = WORKFLOW.split("name: Restore Rust and Gradle wrapper caches", 1)[1].split(
        "name: Initialize Android Worker", 1
    )[0]
    assert "id: rust-and-gradle-cache" in block
    assert '$HOME/.gradle/wrapper/dists/gradle-9.6.1-bin' in block
    assert "secrets." not in block
    assert "curl " not in block
    assert "gradle --stop" not in block
