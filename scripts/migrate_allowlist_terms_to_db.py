#!/usr/bin/env python3
"""One-time migration of historical license obligations into SQLite decisions.

After this migration, the JSON source is not consulted by the Arena service.
Dry-run by default; --apply validates and backs up before atomic updates.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
from pathlib import Path


def _source(path: Path) -> dict:
    data = json.loads(path.read_text(encoding='utf-8'))
    if data.get('schema_version') != 1:
        raise ValueError('Nieznany format archiwalnego rekordu')
    entries = data.get('models')
    if not isinstance(entries, list) or len(entries) != len({x.get('model_id') for x in entries}):
        raise ValueError('Niespójny archiwalny rejestr')
    return {m['model_id']: m for m in entries}


def _plan(c: sqlite3.Connection, archive: dict) -> list[tuple[int, dict]]:
    c.row_factory = sqlite3.Row
    if c.execute("SELECT COUNT(*) FROM tournaments WHERE status NOT IN ('planned','completed')").fetchone()[0]:
        raise ValueError('Wykryto działający turniej')
    rows = c.execute('''SELECT d.model_id,d.status,d.repo_id,d.revision,d.snapshot_json,
           v.license,v.license_verified
        FROM model_admission_decisions d
        JOIN model_versions v ON v.id=(
            SELECT MAX(v2.id) FROM model_versions v2 WHERE v2.model_id=d.model_id)
        ORDER BY d.model_id''').fetchall()
    if set(archive) != {r['model_id'] for r in rows}:
        raise ValueError('Zmieniono zestaw dopuszczonych modeli')
    changes = []
    for r in rows:
        legacy = archive[r['model_id']]
        snap = json.loads(r['snapshot_json'])
        terms = legacy.get('license_requirements')
        restrictions = legacy.get('restrictions')
        if not isinstance(terms, dict) or not isinstance(restrictions, list):
            raise ValueError('Brak kompletnych warunków w archiwum')
        if (r['status'], r['repo_id'], r['revision']) != (
            'admitted', legacy['repo_id'], legacy['revision']):
            raise ValueError('Decyzja w bazie nie odpowiada archiwalnemu wpisowi')
        if (snap.get('model_id'), snap.get('repo_id'), snap.get('revision'),
            snap.get('status'), str(snap.get('license', '')).lower()) != (
            r['model_id'], r['repo_id'], r['revision'], legacy['status'],
            str(r['license']).lower()):
            raise ValueError('Migawka decyzji nie odpowiada aktualnej wersji')
        if str(terms.get('manifest_license', '')).lower() != str(r['license']).lower():
            raise ValueError('Nieprawidłowa licencja w archiwum')
        if r['license_verified'] != 1:
            raise ValueError('Licencja wersji niezatwierdzona')
        updated = dict(snap)
        updated['restrictions'] = restrictions
        updated['license_requirements'] = terms
        updated['owner_admission'] = legacy.get('owner_admission')
        if updated != snap:
            changes.append((r['model_id'], updated))
    return changes


def run(db: Path, archive_path: Path, *, apply: bool = False,
        backup_dir: Path | None = None) -> dict:
    archive = _source(archive_path)
    with sqlite3.connect(f'file:{db}?mode=ro', uri=True) as c:
        changes = _plan(c, archive)
    result = {'applied': False, 'changed_model_ids': [mid for mid, _ in changes], 'backup': None}
    if not apply or not changes:
        return result
    backup_dir = backup_dir or db.parent / 'backups'
    backup_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = backup_dir / ('pre-db-only-allowlist-%s-%d.sqlite3' %
                           (time.strftime('%Y%m%dT%H%M%SZ',time.gmtime()),os.getpid()))
    if target.exists():
        raise FileExistsError(str(target))
    with sqlite3.connect(f'file:{db}?mode=ro', uri=True) as src, sqlite3.connect(target) as dst:
        src.backup(dst)
        if dst.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise RuntimeError('Nieprawidłowa kopia bezpieczeństwa')
    target.chmod(0o600)
    result['backup'] = str(target)
    with sqlite3.connect(db, timeout=30) as c:
        c.execute('BEGIN IMMEDIATE')
        locked = _plan(c, archive)
        if locked != changes:
            raise ValueError('Zmiana bazy w trakcie operacji')
        for mid, updated in changes:
            count = c.execute("UPDATE model_admission_decisions SET snapshot_json=? WHERE model_id=? AND status='admitted'",
                              (json.dumps(updated,ensure_ascii=False,sort_keys=True),mid)).rowcount
            if count != 1:
                raise ValueError('Niespodziewana zmiana decyzji')
    result['applied'] = True
    return result


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--db',required=True,type=Path)
    parser.add_argument('--legacy-json',required=True,type=Path,
                        help='Read-once archival source, never used by live app')
    parser.add_argument('--backup-dir',type=Path)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    os.umask(0o077)
    print(json.dumps(run(args.db,args.legacy_json,apply=args.apply,
                         backup_dir=args.backup_dir),ensure_ascii=False,sort_keys=True))


if __name__=='__main__':
    main()
