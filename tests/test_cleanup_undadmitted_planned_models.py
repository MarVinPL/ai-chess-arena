"""Regression tests for Issue #263: cleanup without tournament history loss."""
from __future__ import annotations

import importlib.util
import sqlite3
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "issue263_cleanup", ROOT / "scripts" / "cleanup_undadmitted_planned_models.py"
)
cleanup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cleanup)


class CleanupNonAdmittedModelsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.db = self.base / "arena.sqlite3"
        self.models = self.base / "models"
        self.models.mkdir()
        with sqlite3.connect(self.db) as c:
            c.execute("PRAGMA foreign_keys=ON")
            c.executescript("""
            CREATE TABLE league_models(id INTEGER PRIMARY KEY,name TEXT,active INTEGER,format TEXT);
            CREATE TABLE model_versions(
              id INTEGER PRIMARY KEY,model_id INTEGER REFERENCES league_models(id),
              filename TEXT,draft_filename TEXT,status TEXT,
              managed_copy INTEGER,draft_managed_copy INTEGER);
            CREATE TABLE model_admission_decisions(model_id INTEGER PRIMARY KEY,status TEXT);
            CREATE TABLE model_admission_history(model_id INTEGER,event TEXT);
            CREATE TABLE tournaments(id INTEGER PRIMARY KEY,status TEXT);
            CREATE TABLE tournament_entries(
              tournament_id INTEGER REFERENCES tournaments(id),
              model_id INTEGER REFERENCES league_models(id),
              version_id INTEGER REFERENCES model_versions(id),
              PRIMARY KEY(tournament_id,model_id));
            CREATE TABLE tournament_pairings(
              id INTEGER PRIMARY KEY,
              tournament_id INTEGER REFERENCES tournaments(id),
              white_model_id INTEGER REFERENCES league_models(id),
              black_model_id INTEGER REFERENCES league_models(id),
              leg INTEGER, status TEXT,game_id INTEGER,
              winner_model_id INTEGER REFERENCES league_models(id));
            CREATE TABLE games(id INTEGER PRIMARY KEY,tournament_id INTEGER,white_model TEXT);
            """)
            for mid in range(1, 11):
                c.execute("INSERT INTO league_models VALUES(?, ?, 1,'PURE_AI')",
                          (mid, "model-" + str(mid)))
                c.execute("INSERT INTO model_versions VALUES(?,?,?,NULL,'catalogued',0,0)",
                          (mid, mid, f"model-{mid}.gguf"))
                if mid <= 8:
                    c.execute("INSERT INTO model_admission_decisions VALUES(?,'admitted')", (mid,))
                    c.execute("INSERT INTO model_admission_history VALUES(?,'admitted')", (mid,))
            for tid, status in ((5, "completed"), (7, "planned"), (8, "planned")):
                c.execute("INSERT INTO tournaments VALUES(?,?)", (tid, status))
            for tid in (7, 8):
                for mid in range(1, 11):
                    c.execute("INSERT INTO tournament_entries VALUES(?,?,?)", (tid, mid, mid))
                for a in range(1, 11):
                    for b in range(a+1, 11):
                        c.execute("INSERT INTO tournament_pairings(tournament_id,white_model_id,black_model_id,leg,status) VALUES(?,?,?,1,'pending')", (tid, a, b))
                        c.execute("INSERT INTO tournament_pairings(tournament_id,white_model_id,black_model_id,leg,status) VALUES(?,?,?,2,'pending')", (tid, b, a))
            for mid in (1,2):
                c.execute("INSERT INTO tournament_entries VALUES(5,?,?)", (mid, mid))
            c.execute("INSERT INTO games VALUES(1,5,'model-1')")
            c.execute("INSERT INTO tournament_pairings(tournament_id,white_model_id,black_model_id,leg,status,game_id) VALUES(5,1,2,1,'completed',1)")

    def tearDown(self):
        self.temp.cleanup()

    def test_dry_run_is_read_only_and_specific(self):
        before = self.db.read_bytes()
        result = cleanup.run(self.db, self.models)
        self.assertEqual(self.db.read_bytes(), before)
        self.assertEqual(result["withdrawn_model_ids"], [9,10])
        self.assertEqual(result["removed_entries"], 4)
        self.assertEqual(result["removed_pairings"], 68)
        self.assertIsNone(result["backup"])
        self.assertFalse(result["applied"])

    def test_apply_has_valid_backup_preserves_history_and_is_idempotent(self):
        result = cleanup.run(self.db, self.models, apply=True, backup_dir=self.base / "backups")
        self.assertTrue(result["applied"])
        with sqlite3.connect(result["backup"]) as c:
            self.assertEqual(c.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(c.execute("SELECT COUNT(*) FROM league_models").fetchone()[0], 10)
        with sqlite3.connect(self.db) as c:
            c.execute("PRAGMA foreign_keys=ON")
            self.assertEqual(c.execute("SELECT COUNT(*) FROM league_models").fetchone()[0], 8)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM model_versions").fetchone()[0], 8)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM model_admission_decisions").fetchone()[0], 8)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM model_admission_history").fetchone()[0], 8)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM games").fetchone()[0], 1)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM tournament_pairings WHERE tournament_id=5").fetchone()[0], 1)
            for tid in (7,8):
                self.assertEqual(c.execute("SELECT COUNT(*) FROM tournament_entries WHERE tournament_id=?", (tid,)).fetchone()[0], 8)
                self.assertEqual(c.execute("SELECT COUNT(*) FROM tournament_pairings WHERE tournament_id=?", (tid,)).fetchone()[0], 56)
            self.assertEqual(c.execute("PRAGMA quick_check").fetchone()[0], "ok")
            self.assertEqual(c.execute("PRAGMA foreign_key_check").fetchall(), [])
        again = cleanup.run(self.db, self.models, apply=True, backup_dir=self.base / "backups")
        self.assertFalse(again["applied"])
        self.assertEqual(again["withdrawn_model_ids"], [])
        self.assertIsNone(again["backup"])

    def test_reject_existing_file_without_changes(self):
        target = self.models / "model-9.gguf"
        target.write_bytes(b"keep")
        with self.assertRaisesRegex(ValueError, "Lokalny artefakt"):
            cleanup.run(self.db, self.models, apply=True)
        self.assertEqual(self._models_count(), 10)

    def test_reject_existing_completed_link_without_changes(self):
        with sqlite3.connect(self.db) as c:
            c.execute("INSERT INTO tournament_entries VALUES(5,9,9)")
        with self.assertRaisesRegex(ValueError, "rozpoczętych lub zakończonych"):
            cleanup.run(self.db, self.models, apply=True)
        self.assertEqual(self._models_count(), 10)

    def test_reject_unexpected_reference_without_changes(self):
        with sqlite3.connect(self.db) as c:
            c.execute("CREATE TABLE external_cache(id INTEGER PRIMARY KEY,model_id INTEGER)")
            c.execute("INSERT INTO external_cache VALUES(1,10)")
        with self.assertRaisesRegex(ValueError, "Nieprzewidziane odwołanie"):
            cleanup.run(self.db, self.models, apply=True)
        self.assertEqual(self._models_count(), 10)

    def test_reject_running_tournament_without_changes(self):
        with sqlite3.connect(self.db) as c:
            c.execute("INSERT INTO tournaments VALUES(9,'running')")
        with self.assertRaisesRegex(ValueError, "aktywny"):
            cleanup.run(self.db, self.models, apply=True)
        self.assertEqual(self._models_count(), 10)

    def test_reject_unplayed_pairing_changed_to_running(self):
        with sqlite3.connect(self.db) as c:
            c.execute("UPDATE tournament_pairings SET status='running' WHERE tournament_id=7 AND white_model_id=9")
        with self.assertRaisesRegex(ValueError, "nietypowe parowania"):
            cleanup.run(self.db, self.models, apply=True)

    def test_reject_changed_admission_set(self):
        with sqlite3.connect(self.db) as c:
            c.execute("INSERT INTO model_admission_decisions VALUES(9,'admitted')")
        with self.assertRaisesRegex(ValueError, "zestaw decyzji"):
            cleanup.run(self.db, self.models, apply=True)

    def _models_count(self):
        with sqlite3.connect(self.db) as c:
            return c.execute("SELECT COUNT(*) FROM league_models").fetchone()[0]


if __name__ == "__main__":
    unittest.main()
