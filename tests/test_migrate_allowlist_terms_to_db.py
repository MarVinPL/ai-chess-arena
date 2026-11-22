"""The one-time import preserves the SQLite admission identity and license terms."""
import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('db_migration',
                                              ROOT / 'scripts/migrate_allowlist_terms_to_db.py')
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


class MigrateTermsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = self.root / 'a.sqlite3'
        self.old = self.root / 'old.json'
        self.rev = 'a' * 40
        self.snap = {'model_id': 6, 'repo_id': 'org/age-model', 'revision': self.rev,
                     'status': 'restricted', 'admission_scope': ['PURE_AI'], 'license': 'apache-2.0'}
        self.legacy = {'schema_version': 1, 'models': [{
            'model_id': 6, 'repo_id': 'org/age-model', 'revision': self.rev,
            'status': 'restricted', 'owner_admission': 'issue-259-2026-11-22',
            'license_requirements': {'manifest_license': 'apache-2.0',
                                     'license_verified_required': True,
                                     'obligations': ['Minimum 18 lat']},
            'restrictions': ['Model wyłącznie 18+'],
        }]}
        self.old.write_text(json.dumps(self.legacy))
        with sqlite3.connect(self.db) as c:
            c.executescript("""
                CREATE TABLE tournaments(id INTEGER PRIMARY KEY,status TEXT);
                CREATE TABLE model_admission_decisions(
                    model_id INTEGER PRIMARY KEY,status TEXT,
                    repo_id TEXT,revision TEXT,snapshot_json TEXT);
                CREATE TABLE model_versions(
                    id INTEGER PRIMARY KEY,model_id INTEGER,license TEXT,license_verified INTEGER);
            """)
            c.execute("INSERT INTO model_versions VALUES(1,6,'apache-2.0',1)")
            c.execute("INSERT INTO model_admission_decisions VALUES(6,'admitted',?,?,?)",
                      ('org/age-model', self.rev, json.dumps(self.snap)))

    def tearDown(self):
        self.tmp.cleanup()

    def test_dry_run_does_not_modify_database(self):
        before = self.db.read_bytes()
        result = migration.run(self.db, self.old)
        self.assertEqual(result['changed_model_ids'], [6])
        self.assertFalse(result['applied'])
        self.assertEqual(before, self.db.read_bytes())

    def test_apply_copies_terms_atomically_and_is_idempotent(self):
        result = migration.run(self.db, self.old, apply=True, backup_dir=self.root / 'backups')
        self.assertTrue(result['applied'])
        with sqlite3.connect(result['backup']) as c:
            before = json.loads(c.execute('SELECT snapshot_json FROM model_admission_decisions').fetchone()[0])
            self.assertNotIn('restrictions', before)
        with sqlite3.connect(self.db) as c:
            new = json.loads(c.execute('SELECT snapshot_json FROM model_admission_decisions').fetchone()[0])
            self.assertEqual(new['restrictions'], ['Model wyłącznie 18+'])
            self.assertEqual(new['license_requirements']['obligations'], ['Minimum 18 lat'])
            self.assertEqual(new['revision'], self.rev)
        second = migration.run(self.db, self.old, apply=True)
        self.assertEqual(second['changed_model_ids'], [])
        self.assertIsNone(second['backup'])

    def test_stale_revision_is_rejected_without_mutation(self):
        self.legacy['models'][0]['revision'] = 'b' * 40
        self.old.write_text(json.dumps(self.legacy))
        with self.assertRaisesRegex(ValueError, 'nie odpowiada'):
            migration.run(self.db, self.old, apply=True)
        self.assertFalse((self.root / 'backups').exists())

    def test_revoked_is_rejected(self):
        with sqlite3.connect(self.db) as c:
            c.execute("UPDATE model_admission_decisions SET status='revoked'")
        with self.assertRaisesRegex(ValueError, 'nie odpowiada'):
            migration.run(self.db, self.old, apply=True)

    def test_running_tournament_is_rejected(self):
        with sqlite3.connect(self.db) as c:
            c.execute("INSERT INTO tournaments VALUES(1,'running')")
        with self.assertRaisesRegex(ValueError, 'działający'):
            migration.run(self.db, self.old, apply=True)


if __name__=='__main__':
    unittest.main()
