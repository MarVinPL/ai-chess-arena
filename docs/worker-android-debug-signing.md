# Android Worker — continuity of test APK signing

This file documents the **temporary debug APK signing key** required to update the originally installed Android Worker without removing its data.

## Verified prior installation

The first APK served from the VPS at `/var/www/arena-apk/` is signed with the Android Debug cert whose SHA-256 fingerprint (hex, no separators) is:

`3f0449ec3a2399ec669cce2dcbd6aa7262671345dfb983c33e2cadf4d1b0da12`

The same cert was independently verified with `keytool` against `/home/debian/.android/debug.keystore`. **The certificate is public; the keystore and private key are not.**

GitHub repository secrets were confirmed to exist, by checking **names only** (not reading secret values):

- `WORKER_ANDROID_DEBUG_KEYSTORE_B64`: base64 of the original VPS keystore file
- `WORKER_ANDROID_DEBUG_STORE_PASSWORD`: debug keystore password
- `WORKER_ANDROID_DEBUG_KEY_PASSWORD`: debug signing key password

Never put secret values, the keystore, or the private key into the source tree, pull request, logs, release assets, build artifacts, or JavaScript storage.

## Trust separation

The Worker source-filtered CI workflow has separate reusable Android jobs:

- `android`: **pull requests only**, without any signing secrets; ephemeral runner-debug certificates are expected. PR APKs are **not** suitable for upgrading an existing app.
- `android_signed`: **push to `main` only**, with only the three explicitly passed signing secrets. The keystore is restored to `$HOME/.android/debug.keystore` *after* Android project initialization and *before* compiling the debug APK. The source keystore fingerprint and final APK certificate must both match the historical pinned fingerprint.

Both jobs use ordinary GitHub-hosted Actions; no paid action or new release service is required. A missing or mismatching secret **fails the trusted job** rather than generating and publishing an APK with an incompatible key.

The APK is still a **debug build**. The conventional Android debug key has known default passphrases; this is for compatibility with the existing *test* installation only. **Do not use this key for production application releases.** A later production keystore and rollout plan require a separate issue and migration decision.

Current policy (Issue #225): Linux/Windows builds are paused. The signing change does not reactivate them or force app builds merely by editing CI.

## Security, rotation, and recovery

The original VPS keystore must not be world-readable: check permissions of `/home/debian/.android/debug.keystore` and restrict to **0600** under the appropriate operator change-control. Any previously exposed debug keystore should be treated as a *test-only* key, not trusted for production authenticity.

Make an independently protected offline backup of the *original* keystore, consistent with the owner's retention/security policy. If it is lost or replaced, later APKs will not be able to update the already installed app under the same package ID while preserving its data.

Until the coordinator can provide an authenticated, fresh task-idle confirmation and update signatures are fully verified, the app's **automatic update installation stays disabled**. GitHub Releases publication is a separate process and is not created by this signing fix.

Test by changing an actual Worker Android source file on an authorized branch and merging a reviewed PR, then checking that the resulting `main` APK has the pinned certificate fingerprint and correctly updates an installed APK without clearing app data.
