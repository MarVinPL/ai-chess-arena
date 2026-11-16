from pathlib import Path
import importlib.util

SCRIPT = Path(__file__).resolve().parents[1] / "deploy" / "systemd" / "check_sqlite_writable_path.py"
spec = importlib.util.spec_from_file_location("check_sqlite_writable_path", SCRIPT)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)


def test_resolved_database_parent_is_covered_by_writable_directory(tmp_path):
    target = tmp_path / "persistent" / "arena.sqlite3"
    target.parent.mkdir()
    target.touch()
    link = tmp_path / "release" / "arena.sqlite3"
    link.parent.mkdir()
    link.symlink_to(target)

    allowed, resolved_db, required_dir = module.directory_is_writable_by_unit(
        link, [target.parent]
    )

    assert allowed is True
    assert resolved_db == target.resolve()
    assert required_dir == target.parent.resolve()


def test_database_file_allowance_alone_does_not_cover_rollback_journal(tmp_path):
    database = tmp_path / "arena.sqlite3"
    database.touch()

    allowed, _, required_dir = module.directory_is_writable_by_unit(
        database, [database]
    )

    assert allowed is False
    assert required_dir == tmp_path.resolve()


def test_unrelated_writable_directory_does_not_cover_database(tmp_path):
    database_dir = tmp_path / "data"
    unrelated = tmp_path / "other"
    database_dir.mkdir()
    unrelated.mkdir()
    database = database_dir / "arena.sqlite3"
    database.touch()

    allowed, _, _ = module.directory_is_writable_by_unit(database, [unrelated])

    assert allowed is False


def test_parent_directory_allowance_covers_database(tmp_path):
    database_dir = tmp_path / "data" / "live"
    database_dir.mkdir(parents=True)
    database = database_dir / "arena.sqlite3"
    database.touch()

    allowed, _, _ = module.directory_is_writable_by_unit(database, [tmp_path])

    assert allowed is True
