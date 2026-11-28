"""Fail closed unless compiled APK contains the expected backup policy."""
import re
import subprocess
import sys
import zipfile

STORES = {"worker_machine_credential_v1.xml", "worker_update_journals_v1.xml",
          "worker_android_installer_status_v1.xml"}

def verify(aapt, apk):
    def dump(path):
        return subprocess.check_output([aapt, "dump", "xmltree", apk, path], text=True)
    manifest = dump("AndroidManifest.xml")
    app = re.search(r"(?m)^( +)E: application \(.*\n((?:.*\n)*?)\1  E:", manifest)
    if not app:
        raise ValueError("Missing compiled application attributes")
    attrs = app.group(2)
    if not re.search(r"android:allowBackup\([^)]*\)=\(type 0x12\)0x0+$", attrs, re.M):
        raise ValueError("allowBackup is not false")
    resources = subprocess.check_output([aapt, "dump", "resources", apk], text=True)
    with zipfile.ZipFile(apk) as z:
        for attr, name, sections in (
            ("fullBackupContent", "worker_backup_rules", ("full-backup-content",)),
            ("dataExtractionRules", "worker_data_extraction_rules", ("cloud-backup", "device-transfer")),
        ):
            ref = re.search(r"android:" + attr + r"\([^)]*\)=@(0x[0-9a-f]+)", attrs)
            if not ref or not re.search(r"resource\s+" + ref.group(1) + r"\s+[^\s]+:xml/" + name + r"\b", resources):
                raise ValueError("Missing or incorrect resource reference: " + attr)
            paths = [p for p in z.namelist() if p == "res/xml/" + name + ".xml"]
            if len(paths) != 1:
                raise ValueError("Missing compiled backup XML: " + name)
            tree = dump(paths[0])
            for section in sections:
                match = re.search(r"(?m)^( *)E: " + section + r" \([^\n]*\n((?:(?:\1 +[^\n]*| *)\n)*)", tree)
                if not match:
                    raise ValueError("Missing section: " + section)
                exclusions = set()
                for block in re.split(r"(?m)^\s*E: exclude \([^\n]*\n", match.group(2))[1:]:
                    if re.search(r'A: .*domain[^\n]*="sharedpref"', block):
                        path = re.search(r'A: .*path[^\n]*="([^"]+)"', block)
                        if path:
                            exclusions.add(path.group(1))
                if not STORES <= exclusions:
                    raise ValueError("Incomplete exclusions: " + section)
    print("Compiled APK backup policy verified")

if __name__ == "__main__":
    verify(*sys.argv[1:])
