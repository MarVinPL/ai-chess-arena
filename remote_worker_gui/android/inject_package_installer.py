#!/usr/bin/env python3
"""Install the native Android PackageInstaller status receiver after Tauri init.

Tauri regenerates gen/android, so all additions live in versioned sources and
are applied deterministically before compilation. Fail closed on unexpected
manifest layout rather than creating an APK without the receiver.
"""
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "android" / "WorkerInstallStatusReceiver.kt"
VAULT_SOURCE = ROOT / "android" / "WorkerCredentialVault.kt"
QUIC_SOURCE = ROOT / "android" / "WorkerQuicTransport.kt"
ANDROID = ROOT / "src-tauri" / "gen" / "android" / "app" / "src" / "main"
MANIFEST = ANDROID / "AndroidManifest.xml"
TARGET = ANDROID / "java" / "pl" / "marvin" / "aichessarena" / "worker" / SOURCE.name
VAULT_TARGET = TARGET.parent / VAULT_SOURCE.name
ANDROID_NS = "http://schemas.android.com/apk/res/android"
A = f"{{{ANDROID_NS}}}"
ET.register_namespace("android", ANDROID_NS)

def install():
    if not MANIFEST.is_file() or not SOURCE.is_file() or not VAULT_SOURCE.is_file() or not QUIC_SOURCE.is_file():
        raise RuntimeError("Android Tauri init or installer source is missing")
    tree = ET.parse(MANIFEST)
    root = tree.getroot()
    if root.tag != "manifest":
        raise RuntimeError("Not an Android manifest")
    app = root.find("application")
    if app is None:
        raise RuntimeError("Android application element missing")
    cls = "pl.marvin.aichessarena.worker.WorkerInstallStatusReceiver"
    receivers = [r for r in app.findall("receiver") if r.get(A + "name") == cls]
    if len(receivers) > 1:
        raise RuntimeError("Duplicate status receivers")
    if not receivers:
        receiver = ET.SubElement(app, "receiver")
        receiver.set(A + "name", cls)
        receiver.set(A + "exported", "false")
    elif receivers[0].get(A + "exported") != "false":
        raise RuntimeError("Installer receiver must not be exported")
    permission = "android.permission.REQUEST_INSTALL_PACKAGES"
    if not any(p.get(A + "name") == permission for p in root.findall("uses-permission")):
        p = ET.Element("uses-permission")
        p.set(A + "name", permission)
        root.insert(0, p)
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SOURCE, TARGET)
    shutil.copyfile(VAULT_SOURCE, VAULT_TARGET)
    shutil.copyfile(QUIC_SOURCE, VAULT_TARGET.parent / QUIC_SOURCE.name)
    tree.write(MANIFEST, encoding="unicode", xml_declaration=True)
    # Check the output, not only the in-memory tree.
    output = ET.parse(MANIFEST).getroot()
    if not any(r.get(A + "name") == cls and r.get(A + "exported") == "false"
               for r in output.findall("./application/receiver")):
        raise RuntimeError("Installer receiver was not registered")
    if not TARGET.is_file():
        raise RuntimeError("Installer receiver source was not copied")
    if not VAULT_TARGET.is_file():
        raise RuntimeError("Android Keystore credential vault source was not copied")
    if not (VAULT_TARGET.parent / QUIC_SOURCE.name).is_file():
        raise RuntimeError("Android QUIC transport source was not copied")
    print("Android Worker receiver, encrypted token vault and OS QUIC bridge installed")

if __name__ == "__main__":
    install()
