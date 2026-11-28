"""Exercise the actual Tauri manifest injector with isolated generated files."""
import shutil
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
A = "{http://schemas.android.com/apk/res/android}"
STORES = {"worker_machine_credential_v1.xml", "worker_update_journals_v1.xml",
          "worker_android_installer_status_v1.xml"}


class BackupPolicyTests(unittest.TestCase):
    def test_injector_is_idempotent_and_excludes_sensitive_stores(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copytree(ROOT / "remote_worker_gui/android", root / "android")
            main = root / "src-tauri/gen/android/app/src/main"
            main.mkdir(parents=True)
            manifest = main / "AndroidManifest.xml"
            manifest.write_text('<manifest xmlns:android="http://schemas.android.com/apk/res/android"><application android:allowBackup="true"/></manifest>')
            for _ in range(2):
                subprocess.run([sys.executable, str(root / "android/inject_package_installer.py")], check=True, capture_output=True)
            app = ET.parse(manifest).getroot().find("application")
            self.assertEqual(app.get(A + "allowBackup"), "false")
            self.assertEqual(app.get(A + "fullBackupContent"), "@xml/worker_backup_rules")
            self.assertEqual(app.get(A + "dataExtractionRules"), "@xml/worker_data_extraction_rules")
            self.assertEqual(len(app.findall("receiver")), 1)
            legacy = ET.parse(main / "res/xml/worker_backup_rules.xml").getroot()
            modern = ET.parse(main / "res/xml/worker_data_extraction_rules.xml").getroot()
            self.assertEqual(legacy.tag, "full-backup-content")
            self.assertEqual(modern.tag, "data-extraction-rules")
            for section in (legacy, modern.find("cloud-backup"), modern.find("device-transfer")):
                self.assertEqual({e.get("path") for e in section.findall("exclude") if e.get("domain") == "sharedpref"}, STORES)


if __name__ == "__main__":
    unittest.main()
