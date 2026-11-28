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
ANDROID = ROOT / "src-tauri" / "gen" / "android" / "app" / "src" / "main"
MANIFEST = ANDROID / "AndroidManifest.xml"
TARGET = ANDROID / "java" / "pl" / "marvin" / "aichessarena" / "worker" / SOURCE.name
VAULT_TARGET = TARGET.parent / VAULT_SOURCE.name
ANDROID_NS = "http://schemas.android.com/apk/res/android"
A = f"{{{ANDROID_NS}}}"
ET.register_namespace("android", ANDROID_NS)

BACKUP_STORES = (
    "worker_machine_credential_v1.xml",
    "worker_update_journals_v1.xml",
    "worker_android_installer_status_v1.xml",
)
BACKUP_RULES = "worker_backup_rules"
EXTRACTION_RULES = "worker_data_extraction_rules"

def configure_backup(app):
    # Disable cloud backup entirely; explicit exclusions also cover OEM device transfer.
    app.set(A + "allowBackup", "false")
    app.set(A + "fullBackupContent", "@xml/" + BACKUP_RULES)
    app.set(A + "dataExtractionRules", "@xml/" + EXTRACTION_RULES)
    xml_dir = ANDROID / "res" / "xml"
    xml_dir.mkdir(parents=True, exist_ok=True)
    legacy = ET.Element("full-backup-content")
    modern = ET.Element("data-extraction-rules")
    for section in (legacy, ET.SubElement(modern, "cloud-backup"),
                    ET.SubElement(modern, "device-transfer")):
        for name in BACKUP_STORES:
            ET.SubElement(section, "exclude", domain="sharedpref", path=name)
    for name, rules in ((BACKUP_RULES, legacy), (EXTRACTION_RULES, modern)):
        path = xml_dir / (name + ".xml")
        ET.ElementTree(rules).write(path, encoding="utf-8", xml_declaration=True)
        if ET.tostring(ET.parse(path).getroot()) != ET.tostring(rules):
            raise RuntimeError("Backup rules verification failed")

def install():
    if not MANIFEST.is_file() or not SOURCE.is_file() or not VAULT_SOURCE.is_file():
        raise RuntimeError("Android Tauri init or installer source is missing")
    tree = ET.parse(MANIFEST)
    root = tree.getroot()
    if root.tag != "manifest":
        raise RuntimeError("Not an Android manifest")
    app = root.find("application")
    if app is None:
        raise RuntimeError("Android application element missing")
    configure_backup(app)
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
    tree.write(MANIFEST, encoding="unicode", xml_declaration=True)
    # Check the output, not only the in-memory tree.
    output = ET.parse(MANIFEST).getroot()
    if not any(r.get(A + "name") == cls and r.get(A + "exported") == "false"
               for r in output.findall("./application/receiver")):
        raise RuntimeError("Installer receiver was not registered")
    output_app = output.find("application")
    if any(output_app.get(A + key) != value for key, value in (
        ("allowBackup", "false"),
        ("fullBackupContent", "@xml/" + BACKUP_RULES),
        ("dataExtractionRules", "@xml/" + EXTRACTION_RULES),
    )):
        raise RuntimeError("Backup manifest policy verification failed")
    if not TARGET.is_file():
        raise RuntimeError("Installer receiver source was not copied")
    if not VAULT_TARGET.is_file():
        raise RuntimeError("Android Keystore credential vault source was not copied")
    print("Android Worker PackageInstaller receiver and encrypted token vault installed")

if __name__ == "__main__":
    install()
