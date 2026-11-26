#!/usr/bin/env bash
# Offline-safe-to-inspect, reproducible upstream Android llama.cpp packaging.
# Requires GitHub Actions runner tools, Android NDK 27.2 and Ninja/CMake.
# No tokens, model downloads, executable servers or arbitrary caller URL.
set -euo pipefail
umask 022

LLAMA_COMMIT=b1b770d90d94cad9e9ea5b993bdbc383a522fb28
LLAMA_REPO=https://github.com/ggml-org/llama.cpp.git
: "${ANDROID_HOME:?Android SDK is required}"
NDK="${ANDROID_HOME}/ndk/27.2.12479018"
test -f "$NDK/build/cmake/android.toolchain.cmake"

# Invoked from remote_worker_gui after tauri android init.
APP=src-tauri/gen/android/app
test -d "$APP/src/main"
STAGING="${RUNNER_TEMP:?GitHub Actions RUNNER_TEMP required}/arena-llama-android"
test ! -e "$STAGING" || { echo 'Staging path already exists; refuse overwrite' >&2; exit 1; }
mkdir -p "$STAGING/source"
git -C "$STAGING/source" init -q
git -C "$STAGING/source" remote add origin "$LLAMA_REPO"
# Fail closed if this exact object is not available. No branch/tag fallback.
git -C "$STAGING/source" fetch --no-tags --depth 1 origin "$LLAMA_COMMIT"
test "$(git -C "$STAGING/source" rev-parse FETCH_HEAD)" = "$LLAMA_COMMIT"
git -C "$STAGING/source" -c advice.detachedHead=false checkout -q --detach "$LLAMA_COMMIT"
test -s "$STAGING/source/LICENSE"
test -f "$STAGING/source/CMakeLists.txt"

cmake -S "$STAGING/source" -B "$STAGING/build" -G Ninja \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_TOOLCHAIN_FILE="$NDK/build/cmake/android.toolchain.cmake" \
  -DANDROID_ABI=arm64-v8a -DANDROID_PLATFORM=android-31 \
  -DCMAKE_ANDROID_STL_TYPE=c++_static \
  -DBUILD_SHARED_LIBS=ON \
  -DLLAMA_BUILD_TOOLS=OFF -DLLAMA_BUILD_TESTS=OFF \
  -DLLAMA_BUILD_EXAMPLES=OFF -DLLAMA_BUILD_SERVER=OFF \
  -DLLAMA_BUILD_COMMON=OFF -DLLAMA_OPENSSL=OFF \
  -DGGML_NATIVE=OFF -DGGML_OPENMP=OFF \
  -DGGML_VULKAN=OFF -DGGML_CUDA=OFF
cmake --build "$STAGING/build" --parallel 2 --target llama

# CMake upstream builds libllama + ggml dynamic dependencies.
LIB_DIR="$APP/src/main/jniLibs/arm64-v8a"
mkdir -p "$LIB_DIR"
for lib in llama ggml ggml-base ggml-cpu; do
  so="$(find "$STAGING/build" -name "lib${lib}.so" -print -quit)"
  test -n "$so" && test -s "$so" || {
    echo "Missing required upstream library: lib${lib}.so" >&2
    exit 1
  }
  cp -L "$so" "$LIB_DIR/lib${lib}.so"
done

# Compile a small, app-owned open/close shim against the exact pinned ABI.
CXX="$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android31-clang++"
test -x "$CXX"
"$CXX" -std=c++17 -O2 -fPIC -shared -fvisibility=hidden \
  -static-libstdc++ \
  -I"$STAGING/source/include" \
  -I"$STAGING/source/ggml/include" \
  android/arena_llama_bridge.cpp \
  "$(realpath "$LIB_DIR/libllama.so")" -Wl,-rpath,'$ORIGIN' \
  -o "$LIB_DIR/libarena_llama_bridge.so"

NM="$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin/llvm-nm"
READELF="$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin/llvm-readelf"
"$NM" -D --defined-only "$LIB_DIR/libllama.so" > "$STAGING/llama-symbols.txt"
for sym in llama_backend_init llama_model_load_from_file llama_init_from_model llama_decode llama_sampler_init_greedy; do
  # Read the complete symbols file; with pipefail, grep -q on a live nm/awk
  # pipe could SIGPIPE the producer and incorrectly report a missing symbol.
  awk -v expected="$sym" '$NF == expected { found=1 } END { exit !found }' \
    "$STAGING/llama-symbols.txt" || {
    echo "Pinned llama.cpp ABI missing symbol $sym" >&2
    exit 1
  }
done
"$NM" -D --defined-only "$LIB_DIR/libarena_llama_bridge.so" > "$STAGING/bridge-symbols.txt"
for sym in arena_llama_model_open arena_llama_model_close arena_llama_model_is_open arena_llama_generate_greedy; do
  awk -v expected="$sym" '$NF == expected { found=1 } END { exit !found }' \
    "$STAGING/bridge-symbols.txt" || {
    echo "Missing native bridge symbol $sym" >&2; exit 1
  }
done
for so in "$LIB_DIR"/*.so; do
  "$READELF" -h "$so" | grep -Fq 'AArch64'
done
printf 'Packaged pinned llama.cpp: %s\n' "$LLAMA_COMMIT"
(cd "$LIB_DIR" && sha256sum ./*.so)
