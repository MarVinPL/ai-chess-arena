#!/usr/bin/env python3
"""Reconcile the two mismatched owner-admitted Arena versions (IDs 1 and 2).

Dry-run by default. Does not download weights, enable an age-gated model,
modify roster/games or start inference. --apply backs up the SQLite DB first.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import model_allowlist
from model_manifest import load_manifest
DB = ROOT / "arena.sqlite3"
# Only Qwen2.5 and SmolLM2 need metadata repair. No other model is activated or modified.
OPERATIONAL_MODEL_IDS = (1, 2)


def expected_manifest(db_path: Path):
    """Legacy migration validates the recorded owner decision in SQLite."""
    manifest = {int(item["model_id"]): item for item in load_manifest()["models"]}
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as c:
        for model_id in OPERATIONAL_MODEL_IDS:
            row = c.execute("SELECT status,repo_id,revision FROM model_admission_decisions "
                            "WHERE model_id=?", (model_id,)).fetchone()
            expected = manifest[model_id]
            if row is None or (row[0], row[1], row[2]) != (
                    'admitted', expected['repo_id'], expected['revision']):
                raise ValueError(f"Brak decyzji SQLite dla modelu {model_id}")
    return manifest


def plan(connection: sqlite3.Connection, manifest: dict) -> list[dict]:
    """Check all exact pinned content identities before any mutation."""
    connection.row_factory = sqlite3.Row
    running = connection.execute(
        "SELECT COUNT(*) FROM tournaments WHERE status IN ('running','starting','stopping')"
    ).fetchone()[0]
    if running:
        raise ValueError("Turniej w trakcie działania: nie modyfikuj wersji modeli")
    changes = []
    for model_id in OPERATIONAL_MODEL_IDS:
        expected = manifest[model_id]
        row = connection.execute("""SELECT m.active,v.* FROM league_models m
            JOIN model_versions v ON v.id=(SELECT MAX(v2.id) FROM model_versions v2 WHERE v2.model_id=m.id)
            WHERE m.id=?""", (model_id,)).fetchone()
        if row is None or row["active"] != 1 or row["model_id"] != model_id:
            raise ValueError(f"Nieaktywny lub brakujący model {model_id}")
        for key in ("filename", "sha256", "size_bytes"):
            actual = row[key]
            if key == "sha256":
                actual = (actual or "").lower()
            if actual != expected[key]:
                raise ValueError(f"Rozbieżność zawartości dla modelu {model_id}: {key}")
        if str(row["license"]).lower() != str(expected["license"]).lower():
            raise ValueError(f"Rozbieżność licencji dla modelu {model_id}")
        source_prefix = "https://huggingface.co/" + expected["repo_id"]
        if row["source"] != source_prefix and not row["source"].startswith(source_prefix + "/resolve/"):
            raise ValueError(f"Rozbieżność repozytorium dla modelu {model_id}")
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except (ValueError, TypeError) as exc:
            raise ValueError(f"Nieprawidłowe metadane dla modelu {model_id}") from exc
        if not isinstance(metadata, dict):
            raise ValueError(f"Nieprawidłowe metadane dla modelu {model_id}")
        recorded_revision = metadata.get("revision")
        if recorded_revision and recorded_revision != expected["revision"] and model_id != 1:
            raise ValueError(f"Nieoczekiwana rewizja dla modelu {model_id}")
        if model_id == 1 and recorded_revision not in (
            None, "6dd44a1fb35d11b5d1b28902876ce3cc9e882d0e", expected["revision"]
        ):
            raise ValueError("Qwen2.5: nieznana rewizja; wymagana ręczna analiza")
        if metadata.get("repo_id") not in (None, expected["repo_id"]):
            raise ValueError(f"Inne repo w metadanych modelu {model_id}")
        if metadata.get("owner_admission") not in (None, model_allowlist.OWNER_ADMISSION_KEY):
            raise ValueError(f"Sprzeczna wcześniejsza decyzja modelu {model_id}")
        updated = dict(metadata)
        if recorded_revision and recorded_revision != expected["revision"]:
            updated["previous_pinned_revision"] = recorded_revision
        updated.update({
            "repo_id": expected["repo_id"],
            "revision": expected["revision"],
            "download_url": expected["download_url"],
            "source_path": expected["source_path"],
            "owner_admission": model_allowlist.OWNER_ADMISSION_KEY,
            "owner_license_scope": "exact pinned GGUF, PURE_AI; binding terms retained",
        })
        changes.append({
            "model_id": model_id,
            "version_id": row["id"],
            "new_source": source_prefix,
            "metadata": updated,
            "was_verified": row["license_verified"] == 1,
            "needs_change": (row["license_verified"] != 1 or row["source"] != source_prefix
                             or updated != metadata),
        })
    return changes


def backup_db(db_path: Path, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    target = directory / f"issue259-prechange-{stamp}.sqlite3"
    if target.exists():
        raise FileExistsError(str(target))
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as source, sqlite3.connect(target) as dest:
        source.backup(dest)
        if dest.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise RuntimeError("Kopia bazy nie przeszła kontroli integralności")
    target.chmod(0o600)
    return target


def run(db_path: Path, *, apply: bool = False, backup_dir: Path | None = None) -> dict:
    manifest = expected_manifest(db_path)
    if not db_path.is_file():
        raise FileNotFoundError(str(db_path))
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as reader:
        changes = plan(reader, manifest)
    result = {
        "apply": apply,
        "selected_ids": list(OPERATIONAL_MODEL_IDS),
        "age_restricted_model_6": "not_activated",
        "changed_version_ids": [x["version_id"] for x in changes if x["needs_change"]],
        "backup": None,
    }
    if not apply:
        return result
    backup = backup_db(db_path, backup_dir or db_path.parent / "backups")
    result["backup"] = str(backup)
    with sqlite3.connect(db_path, timeout=20) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN IMMEDIATE")
        locked = plan(connection, manifest)
        if [(x["version_id"], x["needs_change"]) for x in changes] != [
            (x["version_id"], x["needs_change"]) for x in locked
        ]:
            raise ValueError("Baza zmieniła się od preflight: przerwano bez zapisu")
        for change in locked:
            if not change["needs_change"]:
                continue
            connection.execute("""UPDATE model_versions SET license_verified=1,
                source=?,metadata_json=? WHERE id=?""",
                (change["new_source"], json.dumps(change["metadata"], ensure_ascii=False),
                 change["version_id"]))
        # Transaction commits only after all version identities have passed.
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DB)
    parser.add_argument("--backup-dir", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run(args.db, apply=args.apply, backup_dir=args.backup_dir),
                     ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
