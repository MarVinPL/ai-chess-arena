# GitHub Actions — Android Worker (#198)

## Continuous integration (CI)

- The entry point is [worker-multiplatform.yml](../.github/workflows/worker-multiplatform.yml). It selects platform builds for actual Worker source changes in pull requests to main and pushes to main. Documentation-only changes do not trigger APK compilation.
- [android-worker-apk.yml](../.github/workflows/android-worker-apk.yml) is a reusable workflow (workflow_call), not a manually dispatchable build. Android ARM64 compilation runs on GitHub-hosted Ubuntu 24.04, not the VPS.
- The build uses the checksum-verified Android CLI, API 36, NDK 27.2.12479018, Rust aarch64-linux-android, npm ci and Tauri Android initialization. It injects the version-controlled native installer receiver and generates the pawn launcher resources.
- npm and Rust caches, pinned setup-action source caches, the Gradle wrapper cache and fingerprinted Gradle modules cache reduce repeated downloads. A cache restore does not prove offline compilation; full cold/warm/offline validation remains in #342.
- PR builds do not receive the original signing key and do not upload the Android APK artifact. Their debug signature is not suitable for updating an existing installation. On trusted main pushes the reusable workflow uses the existing original debug signing key, verifies the pinned certificate and SHA-256, and uploads a 90-day Actions artifact named ai-chess-arena-worker-arm64-debug. The APK filename includes the actual versionName and pre/run number. This artifact is not a published release.

## Authorized GitHub Releases

Use [worker-android-trusted-release.yml](../.github/workflows/worker-android-trusted-release.yml), manually dispatched on main. This is separate from the source-change CI workflow. Inputs: pr_number, source_sha (exact 40-character commit), version, version_code, channel (Latest or Pre-release), test_number.

- Pre-release requires the exact HEAD of an open same-repository PR targeting main, with successful Python push/PR checks and Android PR build on that SHA. Tag: worker-vX.Y.Z-test.N.
- Latest requires the exact merge commit of a merged PR, equal to the workflow's main SHA, with successful manually dispatched Python tests and the signed Android main build on that SHA. Tag: worker-vX.Y.Z.
- Build runs without original signing secrets; the separate sign job uses the protected android-signing environment and checks the package, ARM64 ABI, versionCode and original certificate. Publication is a separate job using the verified artifact; existing releases/tags must not be overwritten. APK, SHA256SUMS and PROVENANCE.txt are published together.
- Never commit the keystore or expose signing secrets. Do not instruct users to uninstall an existing Worker merely to bypass a signature mismatch. A successful CI build, signed release, installation, physical test and production deployment are separate events.

## Evidence and remaining work

The trusted release workflow completed build/sign/publish in [run 46068713039](https://github.com/MarVinPL/ai-chess-arena/actions/runs/46068713039), producing [0.1.3-test.5](https://github.com/MarVinPL/ai-chess-arena/releases/tag/worker-v0.1.3-test.5). This proves the tested Pre-release publication path, not a physical active-game update or a completed Latest release.

- #243: shared guided updater for both channels and monotonically increasing versionCode; the current workflow accepts an expected versionCode but does not yet enforce a global release sequence.
- #342: Gradle dependency cache and offline validation; evaluate the official Android CLI initialization separately from Tauri initialization.
- #311: final physical Android integration, including STOP/ACK, checkpoint, installation and resume.

Use free GitHub-hosted CI for this public repository; no paid services or production deployment without separate authorization.
