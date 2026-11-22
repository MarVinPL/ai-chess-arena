#!/usr/bin/env python3
"""Read-only prerequisite and disk-capacity check for Android APK builds."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
config = json.loads((ROOT / 'src-tauri' / 'tauri.conf.json').read_text())
package = json.loads((ROOT / 'package.json').read_text())
if config['identifier'] != 'pl.marvin.aichessarena.worker' or package['version'] != config['version']:
    raise SystemExit('FAIL: application identifier/version mismatch')
print('Application:', config['identifier'], 'version', config['version'])
checks = {name: bool(shutil.which(binary)) for name, binary in (
    ('Node.js', 'node'), ('npm', 'npm'), ('Rust cargo', 'cargo'),
    ('Java JDK', 'javac'), ('APK signing tools', 'apksigner'))}
sdk = next((Path(value) for name in ('ANDROID_HOME', 'ANDROID_SDK_ROOT')
            if (value := os.environ.get(name)) and Path(value).is_dir()), None)
checks['Android SDK'] = sdk is not None and (sdk / 'platforms').is_dir()
ndk = Path(os.environ['NDK_HOME']) if os.environ.get('NDK_HOME') else None
if ndk is None and sdk is not None and (sdk / 'ndk').is_dir():
    ndk = next((item for item in (sdk / 'ndk').iterdir() if item.is_dir()), None)
checks['Android NDK'] = bool(ndk and (ndk / 'toolchains' / 'llvm').is_dir())
checks['Rust Android target'] = False
if shutil.which('rustup'):
    result = subprocess.run(['rustup', 'target', 'list', '--installed'],
                            capture_output=True, text=True, timeout=10, check=False)
    checks['Rust Android target'] = result.returncode == 0 and any(
        target in result.stdout.splitlines() for target in
        ('aarch64-linux-android', 'armv7-linux-androideabi', 'x86_64-linux-android'))
for name, ready in checks.items():
    print(f'{name}: {"OK" if ready else "MISSING"}')
free_gib = shutil.disk_usage(ROOT).free / (1024 ** 3)
print(f'Free disk: {free_gib:.1f} GiB')
# Conservative safety margin for production VPS; no files are deleted.
if free_gib < 10:
    print('BLOCKED: less than 10 GiB free; do not install SDK/NDK and build caches on this VPS')
    sys.exit(3)
if not all(checks.values()):
    print('NOT READY: missing free Android build dependencies; no APK built')
    sys.exit(2)
print('Preflight passed; APK build and signing still require separate verification')
