#!/usr/bin/env bash
# Issue #249: Android CLI 22.0 is installed by setup-android@v4 with its
# sdkmanager calls disabled. This script is shared by APK CI and SDK smoke CI.
set -euo pipefail
: "${ANDROID_HOME:?ANDROID_HOME must be set by setup-android}"
command -v android >/dev/null
android --sdk="$ANDROID_HOME" sdk install \
  platform-tools \
  platforms/android-36 \
  build-tools/36.0.0 \
  ndk/27.2.12479018

test -s "$ANDROID_HOME/platforms/android-36/android.jar"
test -x "$ANDROID_HOME/build-tools/36.0.0/apksigner"
test -d "$ANDROID_HOME/ndk/27.2.12479018"
test -x "$ANDROID_HOME/platform-tools/adb"
if [[ -n "${GITHUB_ENV:-}" ]]; then
  printf 'NDK_HOME=%s\n' "$ANDROID_HOME/ndk/27.2.12479018" >> "$GITHUB_ENV"
fi
