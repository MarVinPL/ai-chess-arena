"""Gradle Wrapper distribution is cached independently of Rust via setup-java.

The Tauri Android wrapper is generated only after setup-java runs. Its
version-matched wrapper-properties key pin must exist before cache restore.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github/workflows/android-worker-apk.yml").read_text("utf-8")
WRAPPER = (ROOT / ".github/gradle-cache/gradle-wrapper.properties").read_text("utf-8")


def test_gradle_distribution_uses_approved_setup_java_cache():
    java = WORKFLOW.split("name: Read and validate setup-java gitlink", 1)[1].split(
        "name: Install pinned official Android CLI", 1
    )[0]
    assert "uses: ./.github/actions/setup-java" in java
    assert "Verify setup-java source" in java
    assert "cache-jdk: false" in java
    assert "name: Restore independent Gradle wrapper cache" in java
    assert "path: /home/runner/.gradle/wrapper" in java
    assert "hashFiles('.github/gradle-cache/gradle-wrapper.properties')" in java
    assert "uses: actions/cache@v4" not in WORKFLOW
    assert WORKFLOW.index("name: Restore independent Gradle wrapper cache") < WORKFLOW.index(
        "name: Initialize Android Worker"
    )


def test_generated_wrapper_has_preexisting_96_1_cache_key():
    assert "distributionUrl=https\\://services.gradle.org/distributions/gradle-9.6.1-bin.zip" in WRAPPER
    assert "distributionBase=GRADLE_USER_HOME" in WRAPPER
    assert "zipStoreBase=GRADLE_USER_HOME" in WRAPPER
    assert WORKFLOW.index("name: Restore independent Gradle wrapper cache") < WORKFLOW.index(
        "npx tauri android init --ci --skip-targets-install"
    )


def test_rust_cache_is_independent_of_gradle_wrapper_restore():
    rust = WORKFLOW.split("name: Restore Rust cache", 1)[1].split(
        "name: Report Gradle wrapper cache", 1
    )[0]
    assert "uses: swatinem/rust-cache@d1527c8f67ef9af357290475d4af4b0fab8f12e0" in rust
    assert "cache-directories: ~/.gradle/wrapper/dists" not in rust
    report = WORKFLOW.split("name: Report Gradle wrapper cache", 1)[1].split(
        "name: Initialize Android Worker", 1
    )[0]
    assert "gradle-launcher-9.6.1.jar" in report
    assert "secrets." not in report
    assert "curl " not in report


def test_cache_change_preserves_build_and_signing_contract():
    assert WORKFLOW.index("android/force_extracted_native_libs.py") < WORKFLOW.index("name: Fingerprint generated Gradle dependency inputs")
    assert "android/inject_package_installer.py" in WORKFLOW
    assert "bash android/build_embedded_llama.sh" in WORKFLOW
    assert "path: ~/.gradle/caches/modules-2" in WORKFLOW
    assert "npm ci" in WORKFLOW
    assert "npx tauri android init --ci --skip-targets-install" in WORKFLOW
    assert "python3 ../scripts/retry_http_429.py npx --no-install tauri android build --apk --target aarch64 --ci --debug" in WORKFLOW
    assert "if: github.event_name == 'push' && github.ref == 'refs/heads/main'" in WORKFLOW
    assert "Restore original Android test signing key" in WORKFLOW
    assert "Verify signature and SHA-256" in WORKFLOW
