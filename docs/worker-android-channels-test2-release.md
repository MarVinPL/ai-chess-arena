# Android Worker test.2 — signed, manual channel-selector prerelease

This is a one-shot **test** build and **not** a production distribution of
AI Chess Arena Worker. Source PR #193 contains the merged Issue #243
Latest / Pre-release channel selector, while the full Worker inference
feature remains unmerged.

The trusted workflow is run **manually from main**. It checks out the exact
reviewed source commit 16d4a98bedf08f4f985caed4701e922fb99a9866, without
signing secrets, and compiles an Android ARM64 APK. The build job has only
read-only permissions, never receives secrets and does not persist GitHub
credentials. A **separate, clean signing runner** then verifies the build
artifact's checksum, package, version and ABI, restores the original Android
debug keystore exclusively from GitHub Secrets, and re-signs it. A third
job **without any signing secrets** publishes the verified test prerelease
worker-v0.1.0-test.2 to GitHub Releases. Existing releases are never
overwritten.

Certificate fingerprint SHA-256:
3f0449ec3a2399ec669cce2dcbd6aa7262671345dfb983c33e2cadf4d1b0da12

Package: pl.marvin.aichessarena.worker
Android versionCode: 1000
App versionName: 0.1.0
CPU ABI: arm64-v8a

The versionCode is unchanged. Updating on the actual phone requires opening
the signed APK with Android's package installer; **do not uninstall** the
previous installation or clear data first. This compatibility experiment
is not proof of successful installation until verified on the device.
Stored token/settings should survive a successful same-signature update.

All build tools and public GitHub Actions are free/open-source. Never use
this debug keystore for production signing. Neither selecting the
Pre-release channel nor discovering this prerelease enables automatic
download/installation, or starts remote GGUF computation.

Issue: #245. Base code: #193, selector: #243 / #244.
