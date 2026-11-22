"""Integrity-checked, transactional checkpoints for resumable tournaments."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path

SCHEMA_VERSION = 1


def ensure_schema(db_path: str | Path) -> None:
    with sqlite3.connect(db_path, timeout=10) as db:
        db.execute('''CREATE TABLE IF NOT EXISTS tournament_checkpoints (
            tournament_id INTEGER PRIMARY KEY,
            schema_version INTEGER NOT NULL,
            payload_json TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(tournament_id) REFERENCES tournaments(id) ON DELETE CASCADE
        )''')


def _canonical(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False)


def save(db_path: str | Path, tournament_id: int, payload: dict) -> dict:
    if not isinstance(payload, dict) or payload.get('tournament_id') != int(tournament_id):
        raise ValueError('Checkpoint nie zawiera zgodnego identyfikatora turnieju')
    if payload.get('schema_version') != SCHEMA_VERSION:
        raise ValueError('Nieobsługiwana wersja checkpointu')
    encoded = _canonical(payload)
    digest = hashlib.sha256(encoded.encode('utf-8')).hexdigest()
    stamp = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    # SQLite transaction is atomic; synchronous=FULL requests durable commit.
    with sqlite3.connect(db_path, timeout=10, isolation_level='IMMEDIATE') as db:
        db.execute('PRAGMA synchronous=FULL')
        db.execute('''INSERT INTO tournament_checkpoints
            (tournament_id,schema_version,payload_json,sha256,updated_at)
            VALUES(?,?,?,?,?) ON CONFLICT(tournament_id) DO UPDATE SET
            schema_version=excluded.schema_version,payload_json=excluded.payload_json,
            sha256=excluded.sha256,updated_at=excluded.updated_at''',
            (int(tournament_id), SCHEMA_VERSION, encoded, digest, stamp))
        row = db.execute('SELECT schema_version,payload_json,sha256 FROM tournament_checkpoints WHERE tournament_id=?',
                         (int(tournament_id),)).fetchone()
        if not row or row[0] != SCHEMA_VERSION or row[1] != encoded or row[2] != digest:
            raise RuntimeError('Nie udało się potwierdzić trwałego zapisu checkpointu')
        if hashlib.sha256(row[1].encode('utf-8')).hexdigest() != row[2]:
            raise RuntimeError('Kontrola integralności checkpointu nie powiodła się')
    return {'tournament_id': int(tournament_id), 'sha256': digest, 'updated_at': stamp}


def load(db_path: str | Path, tournament_id: int) -> dict | None:
    with sqlite3.connect(db_path, timeout=10) as db:
        row = db.execute('SELECT schema_version,payload_json,sha256 FROM tournament_checkpoints WHERE tournament_id=?',
                         (int(tournament_id),)).fetchone()
    if row is None:
        return None
    version, encoded, digest = row
    if version != SCHEMA_VERSION:
        raise ValueError('Checkpoint ma nieobsługiwaną wersję; wznowienie zablokowane')
    actual = hashlib.sha256(encoded.encode('utf-8')).hexdigest()
    if actual != digest:
        raise ValueError('Checkpoint jest uszkodzony; wznowienie zablokowane')
    try:
        payload = json.loads(encoded)
    except json.JSONDecodeError as exc:
        raise ValueError('Checkpoint zawiera niepoprawny JSON; wznowienie zablokowane') from exc
    if not isinstance(payload, dict) or payload.get('tournament_id') != int(tournament_id):
        raise ValueError('Checkpoint ma niezgodny identyfikator turnieju')
    return payload


def delete(db_path: str | Path, tournament_id: int) -> None:
    with sqlite3.connect(db_path, timeout=10) as db:
        db.execute('DELETE FROM tournament_checkpoints WHERE tournament_id=?', (int(tournament_id),))


def save_in_transaction(db: sqlite3.Connection, tournament_id: int, payload: dict, *, expected_sha256: str) -> dict:
    """Compare-and-swap checkpoint inside caller-owned SQLite transaction.

    Used for crash-safe remote result application. Caller must own an IMMEDIATE
    transaction and atomically acknowledge the result in the same database.
    """
    if not isinstance(tournament_id, int) or isinstance(tournament_id, bool) or tournament_id <= 0:
        raise ValueError('Invalid tournament ID')
    if not isinstance(payload, dict) or payload.get('tournament_id') != tournament_id or payload.get('schema_version') != SCHEMA_VERSION:
        raise ValueError('Invalid checkpoint payload')
    if not isinstance(expected_sha256, str) or len(expected_sha256) != 64:
        raise ValueError('Invalid expected checkpoint hash')
    row = db.execute('SELECT sha256,payload_json FROM tournament_checkpoints WHERE tournament_id=?', (tournament_id,)).fetchone()
    if row is None or row[0] != expected_sha256 or hashlib.sha256(row[1].encode('utf-8')).hexdigest() != row[0]:
        raise ValueError('Checkpoint changed or corrupted; remote result rejected')
    encoded = _canonical(payload)
    digest = hashlib.sha256(encoded.encode('utf-8')).hexdigest()
    stamp = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    updated = db.execute('''UPDATE tournament_checkpoints SET schema_version=?,payload_json=?,sha256=?,updated_at=?
        WHERE tournament_id=? AND sha256=?''', (SCHEMA_VERSION, encoded, digest, stamp, tournament_id, expected_sha256))
    if updated.rowcount != 1:
        raise ValueError('Checkpoint concurrently modified')
    return {'tournament_id': tournament_id, 'sha256': digest, 'updated_at': stamp}
