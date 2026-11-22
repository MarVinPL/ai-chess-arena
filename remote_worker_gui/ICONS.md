# Worker application icons (Android, Linux, Windows)

This Worker uses **one existing two-color design** across all three supported platforms. The original 64 × 64 artwork was scaled with nearest-neighbor interpolation; its shapes and RGBA colors (`#121e31` and `#818cf8`) were not changed. No external art, paid assets, or branding were added.

## Committed desktop icons

All sources live under `remote_worker_gui/src-tauri/icons/`:

| File | Size | Used by |
| --- | --- | --- |
| `icon.png` | 512 × 512 | Shared master for Android launcher generation and Linux |
| `32x32.png` | 32 × 32 | Linux |
| `128x128.png` | 128 × 128 | Linux |
| `128x128@2x.png` | 256 × 256 | Linux HiDPI |
| `icon.ico` | PNG-backed 16, 24, 32, 48, 64, 256 px layers | Windows |

Tauri's `bundle.icon` configuration names these five files. Bundling, publishing, and automatic installation remain disabled (`bundle.active=false`).

## Android launcher resources

Android icons are generated **during the Android build**, not stored in Git as a partial `src-tauri/gen/android/` Gradle project. In `.github/workflows/android-worker-apk.yml`, after `npx tauri android init --ci` creates the Android project, `npx tauri icon src-tauri/icons/icon.png` populates the Android launcher's mipmap density variants, including the adaptive-icon foreground. CI checks for both `mipmap-mdpi/ic_launcher.png` and `mipmap-xxxhdpi/ic_launcher_foreground.png` before compiling the APK.

This avoids depending on the runner's default Tauri app icon. Icons do not interact with the Android Keystore, signing material, token store, or any other application's settings. A debug APK still needs separate signing and upgrade-continuity verification before it can replace an installed APK.

## Build selection and checks

The stacked Worker workflow selects every platform for changes to the **shared master** `icons/icon.png`, Linux for Linux PNG variants, and Windows for changes only to `icons/icon.ico`. Editing CI YAML or documentation alone must not start an application build. Android icons are generated from the source master on the next relevant Android compilation; no build is forced by these instructions.

Run `python -m pytest -q tests/test_worker_icon_assets.py` to verify format, size, color preservation, ICO layer sizes, Tauri configuration, Android generation order, and selective build rules. The tests use only the Python standard library and pytest, no Pillow at build time.

Official guide: https://v2.tauri.app/develop/icons/

**Source governance:** this change is PR-only and stacked on #214/#210; no unfinished Worker implementation is merged to `main` just to distribute icons.
