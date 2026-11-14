import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from model_manifest import ManifestError, apply_manifest, load_manifest, validate_manifest


class ModelManifestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "arena.sqlite3"
        with sqlite3.connect(self.db) as connection:
            connection.executescript("""
                CREATE TABLE league_models(id INTEGER PRIMARY KEY, name TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1);
                CREATE TABLE model_versions(
                    id INTEGER PRIMARY KEY, model_id INTEGER NOT NULL, source TEXT NOT NULL,
                    filename TEXT, sha256 TEXT, size_bytes INTEGER, metadata_json TEXT NOT NULL DEFAULT '{}',
                    license TEXT NOT NULL DEFAULT '', license_verified INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'catalogued', managed_copy INTEGER NOT NULL DEFAULT 0
                );
            """)
            manifest = load_manifest()
            for item in manifest["models"]:
                connection.execute("INSERT INTO league_models(id,name) VALUES(?,?)",
                                   (item["model_id"], item["repo_id"]))
                repo_url = f"https://huggingface.co/{item['repo_id']}"
                source = repo_url
                if item["model_id"] in (1, 2):
                    source = f"{repo_url}/resolve/{item['revision']}/{item['filename']}"
                protected = item["model_id"] in (9, 10)
                metadata = {"inference": {"thinking": False}}
                if protected:
                    metadata["issue_71_scope_guard"] = "must-remain-unchanged"
                connection.execute(
                    """INSERT INTO model_versions
                       (id,model_id,source,filename,sha256,size_bytes,license_verified,
                        metadata_json,license,status,managed_copy)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (item["model_id"], item["model_id"], source,
                     f"preexisting-{item['model_id']}.gguf" if protected else None,
                     "a" * 64 if protected else None,
                     12345 if protected else None, 1, json.dumps(metadata),
                     "preexisting-license" if protected else "",
                     "held-for-issue-71" if protected else "catalogued", 0),
                )

    def tearDown(self):
        self.tmp.cleanup()

    def test_checked_in_manifest_is_complete_and_pinned(self):
        payload = load_manifest()
        self.assertEqual(len(payload["models"]), 10)
        self.assertEqual(len({m["model_id"] for m in payload["models"]}), 10)
        self.assertEqual(len({m["repo_id"] for m in payload["models"]}), 10)
        self.assertTrue(all(len(m["sha256"]) == 64 and len(m["revision"]) == 40
                            for m in payload["models"]))

    def test_incomplete_size_hash_or_revision_is_rejected(self):
        payload = load_manifest()
        for field, value in (("size_bytes", None), ("sha256", "not-a-hash"),
                             ("revision", "main")):
            broken = json.loads(json.dumps(payload))
            broken["models"][0][field] = value
            with self.subTest(field=field), self.assertRaises(ManifestError):
                validate_manifest(broken)

    def test_verified_status_requires_revision_bound_audit_evidence(self):
        payload = load_manifest()
        self.assertEqual(next(m for m in payload["models"] if m["model_id"] == 4)["license_review_status"], "review_required")
        self.assertEqual(next(m for m in payload["models"] if m["model_id"] == 6)["license_review_status"], "verified_restricted")

        missing = json.loads(json.dumps(payload))
        missing_model = next(m for m in missing["models"] if m["model_id"] == 6)
        missing_model.pop("license_audit")
        with self.assertRaises(ManifestError):
            validate_manifest(missing)

        mismatched = json.loads(json.dumps(payload))
        mismatched_model = next(m for m in mismatched["models"] if m["model_id"] == 6)
        mismatched_model["license_audit"]["reviewed_revision"] = "0" * 40
        with self.assertRaises(ManifestError):
            validate_manifest(mismatched)

        unknown = json.loads(json.dumps(payload))
        next(m for m in unknown["models"] if m["model_id"] == 6)["license_review_status"] = "approved"
        with self.assertRaises(ManifestError):
            validate_manifest(unknown)

        validate_manifest(payload)

    def test_draft_head_cannot_be_used_as_the_target_model(self):
        payload = load_manifest()
        broken = json.loads(json.dumps(payload))
        draft_model = next(m for m in broken["models"] if m["model_id"] == 9)
        draft_model["filename"] = "draft-placeholder.gguf"
        draft_model["source_path"] = draft_model["filename"]
        with self.assertRaises(ManifestError):
            validate_manifest(broken)

    def test_manifest_application_persists_metadata_but_does_not_approve_license(self):
        payload = load_manifest()
        with sqlite3.connect(self.db) as connection:
            protected_before = connection.execute(
                "SELECT id,model_id,source,filename,sha256,size_bytes,metadata_json,license,license_verified,status,managed_copy FROM model_versions WHERE model_id IN (9,10) ORDER BY model_id"
            ).fetchall()
        result = apply_manifest(self.db, payload, applied_at="2026-11-14T12:00:00Z")
        self.assertEqual([row["model_id"] for row in result["updated"]], list(range(1, 9)))
        self.assertFalse(result["weights_downloaded"])
        with sqlite3.connect(self.db) as connection:
            rows = connection.execute(
                "SELECT filename,sha256,size_bytes,license,license_verified,status,managed_copy,metadata_json FROM model_versions WHERE model_id BETWEEN 1 AND 8 ORDER BY model_id"
            ).fetchall()
            protected_after = connection.execute(
                "SELECT id,model_id,source,filename,sha256,size_bytes,metadata_json,license,license_verified,status,managed_copy FROM model_versions WHERE model_id IN (9,10) ORDER BY model_id"
            ).fetchall()
        self.assertEqual(protected_after, protected_before)
        self.assertEqual(len(rows), 8)
        for item, row in zip(sorted((m for m in payload["models"] if m["model_id"] <= 8), key=lambda x: x["model_id"]), rows):
            self.assertEqual(row[:4], (item["filename"], item["sha256"], item["size_bytes"], item["license"]))
            self.assertEqual(row[4], 0)
            self.assertEqual(row[5], "catalogued")
            self.assertEqual(row[6], 0)
            metadata = json.loads(row[7])
            self.assertEqual(metadata["inference"], {"thinking": False})
            self.assertEqual(metadata["revision"], item["revision"])
            self.assertEqual(metadata["manifest_audit"]["license_review_status"], item["license_review_status"])
            self.assertEqual(metadata["manifest_audit"]["license_audit"], item.get("license_audit"))

    def test_manifest_application_is_atomic_when_registry_entry_is_missing(self):
        payload = load_manifest()
        with sqlite3.connect(self.db) as connection:
            connection.execute("DELETE FROM model_versions WHERE model_id=8")
        with self.assertRaises(ManifestError):
            apply_manifest(self.db, payload, applied_at="2026-11-14T12:00:00Z")
        with sqlite3.connect(self.db) as connection:
            count = connection.execute("SELECT COUNT(*) FROM model_versions WHERE model_id BETWEEN 1 AND 8 AND filename IS NOT NULL").fetchone()[0]
        self.assertEqual(count, 0)

    def test_import_refuses_to_repoint_an_existing_managed_copy(self):
        payload = load_manifest()
        with sqlite3.connect(self.db) as connection:
            connection.execute("UPDATE model_versions SET managed_copy=1 WHERE model_id=4")
        with self.assertRaises(ManifestError):
            apply_manifest(self.db, payload, applied_at="2026-11-14T12:00:00Z")
        with sqlite3.connect(self.db) as connection:
            row = connection.execute("SELECT filename,license_verified FROM model_versions WHERE model_id=4").fetchone()
        self.assertEqual(row, (None, 1))

    def test_duplicate_repo_or_model_id_is_rejected(self):
        payload = load_manifest()
        broken = json.loads(json.dumps(payload))
        broken["models"][1]["model_id"] = broken["models"][0]["model_id"]
        with self.assertRaises(ManifestError):
            validate_manifest(broken)


if __name__ == "__main__":
    unittest.main(verbosity=2)
