import importlib.util
import sqlite3
from contextlib import closing
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "issue157_sqlite_rollback_drill.py"
SPEC = importlib.util.spec_from_file_location("issue157_sqlite_rollback_drill", MODULE_PATH)
DRILL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DRILL)


def test_online_backup_includes_committed_wal_data(tmp_path):
    source = tmp_path / "source.sqlite3"
    with sqlite3.connect(source) as db:
        db.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY, value TEXT)")
        db.execute("INSERT INTO sample VALUES (1, 'initial')")
    wal = tmp_path / "wal.sqlite3"
    DRILL.online_backup(source, wal)
    with sqlite3.connect(wal) as writer:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0].lower() == "wal"
        writer.execute("UPDATE sample SET value='from-wal' WHERE id=1")
    backup = tmp_path / "backup.sqlite3"
    DRILL.online_backup(wal, backup)
    with closing(DRILL.connect_readonly(backup)) as db:
        assert db.execute("SELECT value FROM sample WHERE id=1").fetchone()[0] == "from-wal"
    assert DRILL.logical_digest(wal) == DRILL.logical_digest(backup)


def test_integrity_checker_reports_clean_database(tmp_path):
    source = tmp_path / "source.sqlite3"
    with sqlite3.connect(source) as db: db.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY)")
    assert DRILL.assert_clean(source, "test")['integrity_check'] == 'ok'
