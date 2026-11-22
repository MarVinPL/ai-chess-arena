# Android APK distribution (planned; not yet built)

The Android edition is a native **single `.apk`** installer with application ID
`pl.marvin.aichessarena.worker`. It is not a PWA and does not require the
split-package `.apks` installer. The existing Tauri 2 / Rust UI prototype
is the starting point, **not** a working Android client or installable APK.

## Free build prerequisites

- OpenJDK 17 (or a JDK version supported by the selected Android Gradle plugin)
- Android SDK command-line tools, platform, build-tools and Android NDK
- Rust toolchain with Android targets and Tauri CLI 2
- Node.js/npm and the existing `remote_worker_gui` sources

Run the read-only check from the repository root:

```sh
python3 remote_worker_gui/scripts/android-preflight.py
```

When the prerequisites are installed, initialize Android support **in the
existing project**, not in a separate application:

```sh
cd remote_worker_gui
npm ci
npx tauri android init
npx tauri android build --apk
```

These are **future build commands**, not evidence of a completed build. Android
Tauri support may require adjustments to native dependencies, especially the
current desktop-oriented `keyring` backend. Use Android Keystore-backed
credential storage; never embed worker tokens in the APK, logs, JavaScript,
or build artifacts. Do not ship until this is implemented and verified.

For releases, use an **offline-protected signing key** and Android's standard
APK signature verification; keep the same signing identity for updates.
Do not commit keystores or passwords. Verify the final APK signature with
`apksigner verify --verbose` and install on a test device only with user
approval. Android updates must be initiated through the platform installer;
no silent installation or security bypasses. Keep production dispatch local
until server and client integration pass the security review.

## VPS preflight (22 Nov 2026)

Installed the free Debian `openjdk-21-jdk-headless` package and verified
`javac 21.0.13.1`. The VPS has approximately 7.7 GiB free after installation,
so the remaining Android SDK/NDK and Gradle dependencies need a disk-space
budget before installation. Avoid downloading full Android Studio or creating
large build caches on the production filesystem without checking capacity.
The desktop `keyring` configuration is not Android-ready; producing a build
must not be mistaken for a secure, usable worker until Android Keystore and
network integration are verified.
