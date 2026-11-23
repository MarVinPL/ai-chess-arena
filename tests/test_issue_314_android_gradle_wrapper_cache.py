"""Regression guard: Gradle wrapper ZIP must not download on every CI build.

GitHub-hosted runners are ephemeral. Rust cache hits are immutable, so the
Gradle distribution must use its own cache entry (free GitHub Actions cache).
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github/workflows/android-worker-apk.yml").read_text(
    encoding="utf-8"
)


def test_gradle_distribution_has_independent_cache_before_tauri_build():
    assert "uses: actions/cache@v4" in WORKFLOW
    assert "path: ~/.gradle/wrapper/dists" in WORKFLOW
    assert "key: gradle-wrapper-${{ runner.os }}-9.6.1-bin-v1" in WORKFLOW
    assert "id: gradle-wrapper-cache" in WORKFLOW
    assert WORKFLOW.index("name: Restore Gradle 9.6.1 distribution") < WORKFLOW.index(
        "name: Initialize Android Worker"
    )
    assert WORKFLOW.index("name: Restore Gradle 9.6.1 distribution") < WORKFLOW.index(
        "name: Compile Android Worker"
    )


def test_rust_cache_is_separate_and_cannot_suppress_gradle_cache_save():
    rust_block = WORKFLOW.split("name: Restore Rust cache", 1)[1].split(
        "name: Report Gradle wrapper cache", 1
    )[0]
    assert "uses: swatinem/rust-cache@v2" in rust_block
    assert "cache-directories: ~/.gradle/wrapper/dists" not in rust_block
    gradle_block = WORKFLOW.split("name: Restore Gradle 9.6.1 distribution", 1)[1].split(
        "name: Restore Rust cache", 1
    )[0]
    assert "uses: actions/cache@v4" in gradle_block
    assert "swatinem/rust-cache" not in gradle_block


def test_cache_reporting_checks_restored_distribution_not_only_directory():
    block = WORKFLOW.split("name: Report Gradle wrapper cache", 1)[1].split(
        "name: Initialize Android Worker", 1
    )[0]
    assert "steps.gradle-wrapper-cache.outputs.cache-hit" in block
    assert "gradle-launcher-9.6.1.jar" in block
    assert "secrets." not in block
    assert "curl " not in block
    assert "gradle --stop" not in block


def test_cache_does_not_change_gradle_or_android_signing_policy():
    assert "npm ci" in WORKFLOW
    assert "npx tauri android init --ci --skip-targets-install" in WORKFLOW
    assert "npx tauri android build --apk --target aarch64 --ci --debug" in WORKFLOW
    assert "if: github.event_name == 'push' && github.ref == 'refs/heads/main'" in WORKFLOW
    assert "Restore original Android test signing key" in WORKFLOW
    assert "Verify signature and SHA-256" in WORKFLOW
    assert "GRADLE_OPTS:" in WORKFLOW
