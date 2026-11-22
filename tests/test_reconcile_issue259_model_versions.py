"""Issue #259: exact-version, backed-up approval migration regression tests."""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from model_manifest import load_manifest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("issue259_reconcile", ROOT / "scripts" / "reconcile_issue259_model_versions.py")
migration = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(migration)


class ReconcileIssue259Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "test.sqlite3"
        self.expected = load_manifest()["models"]
        with sqlite3.connect(self.db) as c:
            c.executescript("""
                CREATE TABLE tournaments(id INTEGER PRIMARY KEY, status TEXT);
                CREATE TABLE league_models(id INTEGER PRIMARY KEY, active INTEGER);
                CREATE TABLE model_versions(
                    id INTEGER PRIMARY KEY,model_id INTEGER,source TEXT,
                    license TEXT,license_verified INTEGER,filename TEXT,
                    sha256 TEXT,size_bytes INTEGER,metadata_json TEXT);
            """)
            for entry in self.expected:
                model_id = entry["model_id"]
                repo = entry["repo_id"]
                revision = entry["revision"]
                source = "https://huggingface.co/" + repo
                metadata = {"repo_id": repo, "revision": revision,
                            "download_url": entry["download_url"]}
                if model_id == 1:
                    metadata.pop("repo_id")
                    metadata["revision"] = "6dd44a1fb35d11b5d1b28902876ce3cc9e882d0e"
                    source += "/resolve/" + metadata["revision"] + "/" + entry["filename"]
                    metadata["download_url"] = source
                if model_id == 2:
                    metadata.pop("repo_id")
                    source = entry["download_url"]
                c.execute("INSERT INTO league_models(id,active) VALUES(?,1)", (model_id,))
                c.execute("""INSERT INTO model_versions(id,model_id,source,license,
                    license_verified,filename,sha256,size_bytes,metadata_json)
                    VALUES(?,?,?,?,?,?,?,?,?)""",
                    (model_id,model_id,source,entry["license"],
                     1 if model_id in (1,2,3) else 0,
                     entry["filename"],entry["sha256"],entry["size_bytes"],
                     json.dumps(metadata)))

    def tearDown(self):
        self.tmp.cleanup()

    def test_dry_run_is_read_only_and_excludes_age_gated_id6(self):
        before = self.db.read_bytes()
        report = migration.run(self.db)
        self.assertFalse(report["apply"])
        self.assertEqual(report["selected_ids"], [1,2,3,4,5,7,8])
        self.assertEqual(report["age_restricted_model_6"], "not_activated")
        self.assertIsNone(report["backup"])
        self.assertEqual(self.db.read_bytes(), before)

    def test_apply_has_backup_repairs_pins_and_is_idempotent(self):
        report = migration.run(self.db, apply=True, backup_dir=Path(self.tmp.name) / "backups")
        backup = Path(report["backup"])
        self.assertTrue(backup.is_file())
        with sqlite3.connect(backup) as c:
            self.assertEqual(c.execute("SELECT license_verified FROM model_versions WHERE id=4").fetchone()[0],0)
        with sqlite3.connect(self.db) as c:
            for entry in self.expected:
                row = c.execute("SELECT source,license_verified,metadata_json FROM model_versions WHERE id=?",(entry["model_id"],)).fetchone()
                if entry["model_id"] == 6:
                    self.assertEqual(row[1],0)
                    self.assertNotIn("owner_admission",json.loads(row[2]))
                    continue
                self.assertEqual(row[1],1)
                metadata = json.loads(row[2])
                self.assertEqual(metadata["revision"],entry["revision"])
                self.assertEqual(metadata["repo_id"],entry["repo_id"])
                self.assertEqual(metadata["download_url"],entry["download_url"])
                self.assertEqual(metadata["owner_admission"],"issue-259-2026-11-22")
        self.assertEqual(migration.run(self.db)["changed_version_ids"], [])

    def test_wrong_hash_aborts_without_changes(self):
        with sqlite3.connect(self.db) as c:
            c.execute("UPDATE model_versions SET sha256=? WHERE id=7", ("0"*64,))
        with self.assertRaisesRegex(ValueError,"sha256"):
            migration.run(self.db,apply=True,backup_dir=Path(self.tmp.name) / "backups")
        self.assertFalse((Path(self.tmp.name)/"backups").exists())

    def test_running_tournament_aborts_without_changes(self):
        with sqlite3.connect(self.db) as c:
            c.execute("INSERT INTO tournaments(id,status) VALUES(1,'running')")
        with self.assertRaisesRegex(ValueError,"Turniej"):
            migration.run(self.db)


if __name__ == "__main__":
    unittest.main()
