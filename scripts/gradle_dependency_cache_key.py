#!/usr/bin/env python3
"""Fingerprint dependency inputs from an initialized Android Worker."""
import hashlib
from pathlib import Path
import os

root = Path("remote_worker_gui")
android = root / "src-tauri/gen/android"
paths = [root / "package-lock.json", root / "src-tauri/Cargo.lock"]
paths += sorted(p for p in android.rglob("*") if p.is_file()
                and (p.name.endswith((".gradle", ".gradle.kts", ".versions.toml"))
                     or p.name in {"gradle.properties", "gradle-wrapper.properties", "gradle.lockfile"})
                and not any(x in {".gradle", "build", "target"} for x in p.relative_to(android).parts))
if not (android / "app/build.gradle.kts").is_file():
    raise SystemExit("Missing generated Android project")
h = hashlib.sha256(b"gradle-modules-v1\0java21\0android36\0ndk27.2.12479018\0")
for p in paths:
    h.update(p.relative_to(root).as_posix().encode() + b"\0" + hashlib.sha256(p.read_bytes()).digest())
key = f"arena-gradle-modules-v1-{os.environ['RUNNER_OS']}-{os.environ['RUNNER_ARCH']}-{h.hexdigest()}"
print(key)
with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as out:
    print(f"key={key}", file=out)
