"""SQLite-backed tournament allowlist.

The only authority is model_admission_decisions plus pinned model_versions.
No Git checkout, origin/main, JSON allowlist, or repository state is consulted.
"""
from __future__ import annotations

import json
import re
import sqlite3
from urllib.parse import urlsplit

from model_exclusions import is_excluded_model_identity

OWNER_ADMISSION_KEY = 'issue-259-2026-11-22'

_SHA = re.compile(r'^[0-9a-f]{40}$')
_SHA256 = re.compile(r'^[0-9a-f]{64}$')
_REPO = re.compile(r'^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$')


class AllowlistError(ValueError):
    """A stored admission decision or pinned artifact is invalid."""


def _read_mapping(raw: str, *, context: str) -> dict:
    try:
        data = json.loads(raw or '{}')
    except (TypeError, ValueError) as exc:
        raise AllowlistError(f'Nieprawidłowe dane JSON: {context}') from exc
    if not isinstance(data, dict):
        raise AllowlistError(f'Nieprawidłowa struktura JSON: {context}')
    return data


def load_allowlist(connection: sqlite3.Connection) -> dict:
    """Read current admissions from SQLite, preserving their exact version pins.

    A revoked or mismatched decision stays visible as blocked, but it is never
    silently promoted into tournament eligibility. No file-system allowlist.
    """
    if not isinstance(connection, sqlite3.Connection):
        raise TypeError('Wymagane połączenie SQLite do allowlisty')
    cursor = connection.execute('''SELECT d.model_id,d.status AS decision_status,
           d.repo_id,d.revision,d.snapshot_json,
           m.name,m.active,m.format,v.id AS version_id,v.license,v.license_verified,
           v.filename,v.sha256,v.size_bytes,v.source,v.metadata_json
        FROM model_admission_decisions d
        LEFT JOIN league_models m ON m.id=d.model_id
        LEFT JOIN model_versions v ON v.id=(
            SELECT MAX(v2.id) FROM model_versions v2 WHERE v2.model_id=d.model_id)
        ORDER BY d.model_id''')
    columns = [x[0] for x in cursor.description]
    entries = []
    for raw_row in cursor.fetchall():
        row = dict(zip(columns, raw_row))
        mid = row['model_id']
        decision_repo, revision = row['repo_id'], row['revision']
        snap = _read_mapping(row['snapshot_json'], context=f'model {mid}: decision')
        meta = _read_mapping(row['metadata_json'], context=f'model {mid}: version')
        restrictions = snap.get('restrictions', [])
        if not isinstance(restrictions, list):
            restrictions = []
        obligations = snap.get('license_requirements', {}).get('obligations', []) if isinstance(snap.get('license_requirements'), dict) else []
        if not isinstance(obligations, list):
            obligations = []
        scope = snap.get('admission_scope', [])
        reasons = []
        if row['decision_status'] != 'admitted':
            reasons.append('decyzja dopuszczenia została cofnięta')
        if snap.get('status') not in ('eligible', 'restricted') or not isinstance(scope, list) or not scope:
            reasons.append('brak poprawnego zakresu zatwierdzonego w decyzji')
        if snap.get('model_id') != mid or snap.get('repo_id') != decision_repo or snap.get('revision') != revision:
            reasons.append('migawka decyzji nie zgadza się z kluczem modelu')
        if not isinstance(decision_repo, str) or not _REPO.fullmatch(decision_repo):
            reasons.append('nieprawidłowe repozytorium')
        if not isinstance(revision, str) or not _SHA.fullmatch(revision):
            reasons.append('nieprawidłowy SHA przypiętej rewizji')
        if row['active'] != 1 or row['version_id'] is None:
            reasons.append('brak aktywnego modelu lub jego wersji')
        if meta.get('revision') != revision or meta.get('repo_id') != decision_repo:
            reasons.append('rewizja lub repozytorium wersji nie zgadza się z decyzją SQLite')
        if row['license_verified'] != 1 or not isinstance(row['license'], str) or not row['license']:
            reasons.append('licencja wersji nie została zatwierdzona w bazie')
        if str(snap.get('license', '')).casefold() != str(row['license'] or '').casefold():
            reasons.append('licencja wersji nie zgadza się z decyzją')
        if not isinstance(row['sha256'], str) or not _SHA256.fullmatch(row['sha256'].lower()):
            reasons.append('brak poprawnego SHA-256 artefaktu')
        if not isinstance(row['size_bytes'], int) or row['size_bytes'] <= 0 or not isinstance(row['filename'], str) or not row['filename'].lower().endswith('.gguf'):
            reasons.append('niekompletny opis pliku GGUF')
        src = row['source'] or ''
        parsed = urlsplit(src)
        if parsed.scheme != 'https' or parsed.netloc.lower() != 'huggingface.co' or not (
            parsed.path.strip('/') == decision_repo or parsed.path.strip('/').startswith(decision_repo + '/resolve/')):
            reasons.append('źródło pliku nie zgadza się z zatwierdzonym repozytorium')
        url = meta.get('download_url')
        if not isinstance(url, str) or f'/resolve/{revision}/' not in url or not url.startswith('https://huggingface.co/' + decision_repo + '/'):
            reasons.append('brak przypiętego HTTPS URL artefaktu')
        if is_excluded_model_identity(name=row['name'] or '', source=src, metadata=meta):
            reasons.append('repozytorium wyłączone z Areny')
        if not row['format'] in scope:
            reasons.append('format modelu nie jest objęty decyzją')
        status = snap.get('status') if not reasons else 'blocked'
        entries.append({
            'model_id': mid, 'repo_id': decision_repo, 'revision': revision,
            'status': status, 'admission_scope': scope if not reasons else [],
            'license_requirements': {
                'manifest_license': row['license'],
                'license_verified_required': True,
                'obligations': obligations,
            },
            'restrictions': restrictions,
            'license': row['license'],
            'technical_readiness': snap.get('technical_readiness', 'not_evaluated'),
            'technical_blockers': snap.get('technical_blockers', []),
            'block_reason': '; '.join(reasons) if reasons else None,
            'display_name': row['name'] or snap.get('display_name') or decision_repo,
            'decision_status': row['decision_status'],
            'version_id': row['version_id'],
        })
    return {'schema_version': 1, 'source': 'sqlite', 'models': entries}


def tournament_admission_blockers(entry: dict | None, *, model_id: int,
                                  repo_id: str | None, revision: str | None,
                                  license_name: str | None, license_verified: bool,
                                  tournament_format: str) -> list[str]:
    """Only an exact, active SQLite admission can qualify this model version."""
    if is_excluded_model_identity(source=f'https://huggingface.co/{repo_id}' if isinstance(repo_id, str) else '', metadata={'repo_id': repo_id}):
        return ['repozytorium modelu jest wyłączone z AI Chess Arena']
    if not isinstance(entry, dict):
        return ['brak decyzji dopuszczenia w bazie']
    reasons = []
    if entry.get('model_id') != model_id:
        reasons.append('niezgodny identyfikator dopuszczenia w bazie')
    if entry.get('decision_status') != 'admitted' or entry.get('status') not in {'eligible', 'restricted'}:
        reasons.append('model nie ma aktywnej, zgodnej decyzji dopuszczenia w bazie')
    if entry.get('block_reason'):
        reasons.append(entry['block_reason'])
    if tournament_format not in (entry.get('admission_scope') or []):
        reasons.append('model nie jest dopuszczony do tego formatu turnieju')
    if not isinstance(repo_id, str) or repo_id != entry.get('repo_id'):
        reasons.append('repozytorium różni się od dopuszczonego')
    if not isinstance(revision, str) or revision != entry.get('revision'):
        reasons.append('rewizja różni się od dopuszczonej')
    expected_license = (entry.get('license_requirements') or {}).get('manifest_license')
    if not isinstance(license_name, str) or license_name.casefold() != str(expected_license or '').casefold():
        reasons.append('licencja różni się od zatwierdzonej')
    if license_verified is not True:
        reasons.append('licencja nie została zatwierdzona')
    return reasons


def is_runtime_ready(entry: dict, *, license_verified: bool,
                     local_file_verified: bool, runtime_healthy: bool) -> bool:
    """Actual ability to run remains separate from admission."""
    return bool(isinstance(entry, dict) and entry.get('status') in {'eligible', 'restricted'}
                and entry.get('technical_readiness') == 'ready'
                and not entry.get('technical_blockers')
                and license_verified and local_file_verified and runtime_healthy)
