"""The stage process must not require metadata access to production storage."""
import os
from pathlib import Path

import pytest
from remote_worker_phone_sandbox import _require_separate_db, PRODUCTION_DB


def test_unprivileged_worker_cannot_stat_production_and_still_uses_own_db(tmp_path, monkeypatch):
    stage = tmp_path / "isolated.sqlite3"
    stage.write_bytes(b"standalone stage fixture")
    original_stat = Path.stat

    def stat_with_production_acl(self, *args, **kwargs):
        if self == PRODUCTION_DB:
            raise PermissionError("production ACL")
        return original_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", stat_with_production_acl)
    _require_separate_db(stage)


def test_non_owned_database_is_rejected(tmp_path, monkeypatch):
    stage = tmp_path / "stolen.sqlite3"
    stage.write_bytes(b"not a real SQLite DB")
    monkeypatch.setattr(os, "geteuid", lambda: -123456)
    with pytest.raises(ValueError, match="not owned"):
        _require_separate_db(stage)


def test_production_absolute_path_is_always_rejected():
    with pytest.raises(ValueError, match="production"):
        _require_separate_db(PRODUCTION_DB)


def test_recovery_upgrades_to_merge_sha_before_starting_service():
    root = Path(__file__).resolve().parents[1]
    contents = (root / "scripts/recover_phone_sandbox_acl.sh").read_text()
    assert 'git -C "$link" reset --hard "$sha"' in contents
    assert contents.index('merge-base --is-ancestor') < contents.index('reset --hard "$sha"')
    assert contents.index('reset --hard "$sha"') < contents.index('systemctl start "$unit"')
    assert 'bootstrap' not in contents
