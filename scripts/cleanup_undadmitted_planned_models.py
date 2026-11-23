#!/usr/bin/env python3
"""Clean up the production registry while preserving admitted models and history.

Dry-run by default.  The only permitted writes are deletion of never-downloaded
non-admitted models and their unplayed fixtures in planned tournaments.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "arena.sqlite3"
DEFAULT_MODELS_DIR = ROOT / "managed-models"
EXPECTED_ADMITTED = frozenset(range(1, 9))


def _ids(count: int) -> str:
    return ",".join("?" for _ in range(count))


def _plan(c: sqlite3.Connection, models_dir: Path) -> dict:
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    if c.execute("PRAGMA foreign_key_check").fetchone():
        raise ValueError("Istniejące naruszenie kluczy obcych")
    if not models_dir.is_dir():
        raise ValueError("Brak katalogu modeli; nie można wykluczyć lokalnych plików")

    running = c.execute(
        "SELECT id FROM tournaments WHERE status NOT IN ('planned','completed')"
    ).fetchall()
    if running:
        raise ValueError("Nieznany lub aktywny stan turnieju; bezpieczne usunięcie niemożliwe")

    admitted = {
        int(row["model_id"])
        for row in c.execute(
            "SELECT model_id FROM model_admission_decisions WHERE status='admitted'"
        )
    }
    if admitted != EXPECTED_ADMITTED:
        raise ValueError("Zmieniono zestaw decyzji admitted; przerwano bez zmian")
    all_models = {int(r["id"]) for r in c.execute("SELECT id FROM league_models")}
    if not admitted.issubset(all_models):
        raise ValueError("Brak modelu z decyzją admitted")
    withdrawn = sorted(all_models - admitted)
    planned = [
        int(r["id"])
        for r in c.execute("SELECT id FROM tournaments WHERE status='planned' ORDER BY id")
    ]

    if not withdrawn:
        return {
            "planned_tournament_ids": planned,
            "retained_model_ids": sorted(admitted),
            "withdrawn_model_ids": [],
            "withdrawn_version_ids": [],
            "removed_entries": 0,
            "removed_pairings": 0,
        }

    slots = _ids(len(withdrawn))
    versions = c.execute(
        f"SELECT id,model_id,filename,draft_filename,status,managed_copy,draft_managed_copy "
        f"FROM model_versions WHERE model_id IN ({slots}) ORDER BY model_id,id",
        withdrawn,
    ).fetchall()
    version_ids = sorted(int(r["id"]) for r in versions)
    if len(versions) != len(withdrawn):
        raise ValueError("Nieoczekiwana liczba wersji modelu; wymagany osobny przegląd")
    for v in versions:
        if v["status"] != "catalogued" or v["managed_copy"] != 0 or v["draft_managed_copy"] != 0:
            raise ValueError("Wersja ma stan pobrania lub własność pliku: operacja zabroniona")
        for key in ("filename", "draft_filename"):
            name = v[key]
            if not name:
                continue
            if not isinstance(name, str) or name in (".", "..") or Path(name).name != name:
                raise ValueError("Niepoprawna ścieżka artefaktu; odmowa")
            if (models_dir / name).exists() or (models_dir / (name + ".part")).exists():
                raise ValueError("Lokalny artefakt istnieje; odmowa automatycznego usuwania")

    params = withdrawn
    non_planned_entries = c.execute(
        f"SELECT COUNT(*) FROM tournament_entries e JOIN tournaments t "
        f"ON t.id=e.tournament_id WHERE e.model_id IN ({slots}) "
        f"AND t.status!='planned'", params
    ).fetchone()[0]
    non_planned_pairings = c.execute(
        f"SELECT COUNT(*) FROM tournament_pairings p JOIN tournaments t "
        f"ON t.id=p.tournament_id WHERE (p.white_model_id IN ({slots}) "
        f"OR p.black_model_id IN ({slots}) OR p.winner_model_id IN ({slots})) "
        f"AND t.status!='planned'", params * 3
    ).fetchone()[0]
    if non_planned_entries or non_planned_pairings:
        raise ValueError("Model ma odwołania w rozpoczętych lub zakończonych turniejach")

    unsafe = c.execute(
        f"SELECT COUNT(*) FROM tournament_pairings p JOIN tournaments t "
        f"ON t.id=p.tournament_id WHERE t.status='planned' "
        f"AND (p.white_model_id IN ({slots}) OR p.black_model_id IN ({slots})) "
        f"AND (p.status!='pending' OR p.game_id IS NOT NULL "
        f"OR p.winner_model_id IS NOT NULL)",
        params * 2,
    ).fetchone()[0]
    if unsafe:
        raise ValueError("Istnieją rozegrane lub nietypowe parowania; odmowa usuwania")

    # Check every model/version reference, including future tables and columns.
    # Only the specifically planned rows and owned version rows may disappear.
    known = {
        ("model_versions", "model_id"),
        ("tournament_entries", "model_id"),
        ("tournament_entries", "version_id"),
        ("tournament_pairings", "white_model_id"),
        ("tournament_pairings", "black_model_id"),
    }
    for table_row in c.execute("SELECT name FROM sqlite_master WHERE type='table'"):
        table = table_row["name"]
        if not table.isidentifier():
            raise ValueError("Nieobsługiwana nazwa tabeli")
        columns = [r["name"] for r in c.execute(f'PRAGMA table_info("{table}")')]
        for col in columns:
            if not (col.endswith("model_id") or col.endswith("version_id")):
                continue
            if (table, col) in known:
                continue
            target_values = version_ids if col.endswith("version_id") else withdrawn
            if target_values and c.execute(
                f'SELECT 1 FROM "{table}" WHERE "{col}" IN ({_ids(len(target_values))}) LIMIT 1',
                target_values,
            ).fetchone():
                raise ValueError(f"Nieprzewidziane odwołanie: {table}.{col}")

    # Every planned tournament must retain precisely the admitted roster.
    for tid in planned:
        present = {
            int(row["model_id"])
            for row in c.execute(
                "SELECT model_id FROM tournament_entries WHERE tournament_id=?", (tid,)
            )
        }
        if present - set(withdrawn) != admitted:
            raise ValueError("Planowany turniej nie zawiera dokładnie dopuszczonej obsady")
        rows = c.execute(
            "SELECT white_model_id,black_model_id,leg FROM tournament_pairings "
            "WHERE tournament_id=?", (tid,)
        ).fetchall()
        expected = {
            record
            for a in admitted for b in admitted if a < b
            for record in ((a, b, 1), (b, a, 2))
        }
        remaining = [
            (r["white_model_id"], r["black_model_id"], r["leg"])
            for r in rows
            if r["white_model_id"] in admitted and r["black_model_id"] in admitted
        ]
        if len(remaining) != len(expected) or set(remaining) != expected:
            raise ValueError("Nieprawidłowy terminarz zatwierdzonych graczy")

    entry_count = c.execute(
        f"SELECT COUNT(*) FROM tournament_entries e JOIN tournaments t "
        f"ON t.id=e.tournament_id WHERE t.status='planned' "
        f"AND e.model_id IN ({slots})", params
    ).fetchone()[0]
    pairing_count = c.execute(
        f"SELECT COUNT(*) FROM tournament_pairings p JOIN tournaments t "
        f"ON t.id=p.tournament_id WHERE t.status='planned' "
        f"AND (p.white_model_id IN ({slots}) OR p.black_model_id IN ({slots}))",
        params * 2,
    ).fetchone()[0]
    return {
        "planned_tournament_ids": planned,
        "retained_model_ids": sorted(admitted),
        "withdrawn_model_ids": withdrawn,
        "withdrawn_version_ids": version_ids,
        "removed_entries": entry_count,
        "removed_pairings": pairing_count,
    }


def backup(source: Path, directory: Path) -> Path:
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    result = directory / f"pre-issue263-{stamp}-{os.getpid()}.sqlite3"
    if result.exists():
        raise FileExistsError(str(result))
    with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as c, sqlite3.connect(result) as dst:
        c.backup(dst)
        if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("Kopia bazy nie przeszła kontroli integralności")
    result.chmod(0o600)
    return result


def run(db: Path, models_dir: Path, *, apply: bool = False,
        backup_dir: Path | None = None) -> dict:
    db = Path(db)
    if not db.is_file():
        raise FileNotFoundError(str(db))
    if not apply:
        with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as c:
            return dict(_plan(c, models_dir), applied=False, backup=None)

    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as c:
        before = _plan(c, models_dir)
    if not before["withdrawn_model_ids"]:
        return dict(before, applied=False, backup=None)

    saved = backup(db, backup_dir or db.parent / "backups")
    with sqlite3.connect(db, timeout=30) as c:
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("BEGIN IMMEDIATE")
        locked = _plan(c, models_dir)
        if locked != before:
            raise ValueError("Baza zmieniła się między planowaniem a zapisem")
        removed = locked["withdrawn_model_ids"]
        slots = _ids(len(removed))
        planned_ids = locked["planned_tournament_ids"]
        tids = _ids(len(planned_ids))
        if planned_ids:
            p = c.execute(
                f"DELETE FROM tournament_pairings WHERE tournament_id IN ({tids}) "
                f"AND (white_model_id IN ({slots}) OR black_model_id IN ({slots}))",
                planned_ids + removed * 2,
            ).rowcount
            e = c.execute(
                f"DELETE FROM tournament_entries WHERE tournament_id IN ({tids}) "
                f"AND model_id IN ({slots})",
                planned_ids + removed,
            ).rowcount
            if (p, e) != (locked["removed_pairings"], locked["removed_entries"]):
                raise ValueError("Niezgodna liczba usuniętych wpisów terminarza")
        versions_deleted = c.execute(
            f"DELETE FROM model_versions WHERE model_id IN ({slots})", removed
        ).rowcount
        models_deleted = c.execute(
            f"DELETE FROM league_models WHERE id IN ({slots})", removed
        ).rowcount
        if versions_deleted != len(locked["withdrawn_version_ids"]) or models_deleted != len(removed):
            raise ValueError("Niezgodna liczba usuniętych wpisów modeli")
        if c.execute("PRAGMA foreign_key_check").fetchone():
            raise ValueError("Naruszenie kluczy obcych po zmianie")
        for tid in planned_ids:
            if c.execute(
                "SELECT COUNT(*) FROM tournament_entries WHERE tournament_id=?", (tid,)
            ).fetchone()[0] != len(EXPECTED_ADMITTED):
                raise ValueError("Błędna liczba uczestników po zmianie")
            if c.execute(
                "SELECT COUNT(*) FROM tournament_pairings WHERE tournament_id=?", (tid,)
            ).fetchone()[0] != len(EXPECTED_ADMITTED) * (len(EXPECTED_ADMITTED) - 1):
                raise ValueError("Błędna liczba partii po zmianie")
    return dict(before, applied=True, backup=str(saved))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--models-dir", type=Path, default=DEFAULT_MODELS_DIR)
    ap.add_argument("--backup-dir", type=Path)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    os.umask(0o077)
    print(json.dumps(run(args.db, args.models_dir, apply=args.apply,
                         backup_dir=args.backup_dir), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
