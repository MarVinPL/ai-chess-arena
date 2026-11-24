#!/usr/bin/env python3
"""Fingerprint Android dependency declarations and the pinned Tauri generators."""
import hashlib
import json
import os
from pathlib import Path
import tomllib

root = Path("remote_worker_gui")
android = root / "src-tauri/gen/android"
if not (android / "app/build.gradle.kts").is_file():
    raise SystemExit("Missing generated Android project")

npm = json.loads((root / "package-lock.json").read_text(encoding="utf-8"))
cli = npm["packages"]["node_modules/@tauri-apps/cli"]
if not cli.get("version") or not cli.get("integrity"):
    raise SystemExit("Unpinned Tauri CLI")
# Include the actual platform-specific CLI package, not the application version.
npm_inputs = {name: {k: p[k] for k in ("version", "resolved", "integrity") if k in p}
              for name, p in npm["packages"].items()
              if name.startswith("node_modules/@tauri-apps/cli")}
cargo = tomllib.loads((root / "src-tauri/Cargo.lock").read_text(encoding="utf-8"))
android_crates = {"wry", "tao", "cargo-mobile2", "jni", "jni-sys", "ndk", "ndk-sys"}
rust_inputs = sorted(
    ({k: p[k] for k in ("name", "version", "source", "checksum") if k in p}
     for p in cargo["package"]
     if p["name"].startswith("tauri") or p["name"] in android_crates),
    key=lambda p: (p["name"], p["version"], p.get("source", "")),
)
if not any(p["name"] == "tauri" for p in rust_inputs):
    raise SystemExit("Missing pinned Tauri crate")
paths = sorted(
    p for p in android.rglob("*") if p.is_file()
    and (p.name.endswith((".gradle", ".gradle.kts", ".toml", ".lockfile"))
         or p.name in {"gradle.properties", "gradle-wrapper.properties",
                       "verification-metadata.xml", "verification-keyring.keys",
                       "verification-keyring.gpg"}
         or ("buildSrc" in p.relative_to(android).parts
             and p.suffix in {".kt", ".java", ".groovy"}))
    and not any(x in {".gradle", "build", "target"} for x in p.relative_to(android).parts)
)
h = hashlib.sha256(b"gradle-modules-v2\0java21\0android36\0ndk27.2.12479018\0")
h.update(json.dumps([npm_inputs, rust_inputs], sort_keys=True, separators=(",", ":")).encode() + b"\0")
for p in paths:
    h.update(p.relative_to(root).as_posix().encode() + b"\0" + hashlib.sha256(p.read_bytes()).digest())
key = f"arena-gradle-modules-v2-{os.environ['RUNNER_OS']}-{os.environ['RUNNER_ARCH']}-{h.hexdigest()}"
print(key)
with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as out:
    print(f"key={key}", file=out)
