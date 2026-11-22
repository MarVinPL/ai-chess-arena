#!/usr/bin/env python3
"""Read-only preflight for a single-APK Tauri Android build."""
import json
import os
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
config = json.loads((ROOT / 'src-tauri' / 'tauri.conf.json').read_text())
package = json.loads((ROOT / 'package.json').read_text())
identifier = config['identifier']
if identifier != 'pl.marvin.aichessarena.worker' or package['version'] != config['version']:
    raise SystemExit('FAIL: application identifier/version mismatch')
print('Application:', identifier, 'version', config['version'])
checks = {
    'Node.js': shutil.which('node'),
    'npm': shutil.which('npm'),
    'Rust cargo': shutil.which('cargo'),
    'Java JDK': shutil.which('javac'),
}
sdk = next((Path(value) for name in ('ANDROID_HOME', 'ANDROID_SDK_ROOT')
            if (value := os.environ.get(name)) and Path(value).is_dir()), None)
checks['Android SDK'] = str(sdk) if sdk else None
checks['Android NDK'] = str(Path(os.environ['NDK_HOME'])) if os.environ.get('NDK_HOME') and Path(os.environ['NDK_HOME']).is_dir() else None
for name, found in checks.items():
    print(f'{name}: {found or "MISSING"}')
if not all(checks.values()):
    print('NOT READY: no Android APK build attempted; install missing free SDK/JDK components first')
    sys.exit(2)
print('Prerequisites detected; run Tauri Android init/build separately, then verify APK signing')
