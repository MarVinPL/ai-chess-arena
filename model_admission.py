"""Auditable SQLite model admission decisions, the sole allowlist authority."""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import model_allowlist

ACTOR = 'arena'
ELIGIBLE_LICENSE_STATES = {'verified_permissive', 'verified_restricted'}


def _now() -> str:
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def evaluate_candidates(*, connection: sqlite3.Connection | None = None,
                        allowlist=None, manifest=None):
    """Evaluate exact stored decisions. The production path reads SQLite only.

    Explicit fixture injection is retained for isolated regression tests.
    """
    if allowlist is None:
        if connection is None:
            raise ValueError('Wymagane połączenie z bazą SQLite')
        entries = model_allowlist.load_allowlist(connection)['models']
        return [{
            'model_id': item['model_id'], 'repo_id': item['repo_id'],
            'revision': item['revision'], 'status': item['status'],
            'admission_scope': item.get('admission_scope', []),
            'license': (item.get('license_requirements') or {}).get('manifest_license'),
            'technical_readiness': item.get('technical_readiness'),
            'restrictions': item.get('restrictions', []),
            'license_requirements': item.get('license_requirements', {}),
            'block_reason': item.get('block_reason'),
            'technical_blockers': item.get('technical_blockers', []),
            'blockers': [item['block_reason']] if item.get('block_reason') else (
                [] if item.get('status') in {'eligible', 'restricted'}
                else ['Model nie ma aktualnej decyzji dopuszczenia.']),
            'eligible': bool(item.get('status') in {'eligible', 'restricted'}
                             and not item.get('block_reason')),
            'display_name': item.get('display_name') or item['repo_id'].rsplit('/', 1)[-1],
        } for item in entries]

    # Historical unit-test contract; never used by the service.
    manifest_by_id = {x['model_id']: x for x in (manifest or {}).get('models', [])}
    candidates = []
    for entry in allowlist.get('models', []):
        source = manifest_by_id.get(entry['model_id'])
        blockers = []
        if source is None:
            blockers.append('Brak wersji modelu w danych testowych.')
        else:
            if (source.get('repo_id'), source.get('revision')) != (entry.get('repo_id'), entry.get('revision')):
                blockers.append('Repozytorium lub rewizja nie zgadza się z wersją modelu.')
            if not all(source.get(key) for key in ('filename', 'sha256', 'size_bytes')):
                blockers.append('Brak kompletnego pliku GGUF.')
            if source.get('license_review_status') not in ELIGIBLE_LICENSE_STATES:
                blockers.append('Licencja bez zatwierdzenia.')
        if entry.get('status') not in {'eligible', 'restricted'}:
            blockers.append('Model bez decyzji dopuszczenia.')
        if not entry.get('admission_scope'):
            blockers.append('Brak zakresu dopuszczenia.')
        if entry.get('technical_readiness') != 'ready':
            blockers.append('Brak zatwierdzenia technicznego w fixture.')
        if entry.get('block_reason'):
            blockers.append(str(entry['block_reason']))
        candidates.append({
            'model_id': entry['model_id'], 'repo_id': entry['repo_id'],
            'revision': entry['revision'], 'status': entry['status'],
            'admission_scope': entry.get('admission_scope', []),
            'license': (entry.get('license_requirements') or {}).get('manifest_license'),
            'technical_readiness': entry.get('technical_readiness'),
            'block_reason': entry.get('block_reason'),
            'technical_blockers': entry.get('technical_blockers', []),
            'blockers': blockers, 'eligible': not blockers,
            'display_name': entry['repo_id'].rsplit('/', 1)[-1],
        })
    return candidates


def list_decisions(connection: sqlite3.Connection, *, allowlist=None, manifest=None):
    candidates = evaluate_candidates(connection=connection, allowlist=allowlist, manifest=manifest)
    current = {row['model_id']: dict(row) for row in connection.execute(
        'SELECT model_id,status,actor,decided_at,reason,repo_id,revision FROM model_admission_decisions'
    )}
    history = {}
    for row in connection.execute('''SELECT model_id,event,actor,occurred_at,reason,repo_id,revision
        FROM model_admission_history ORDER BY id'''):
        history.setdefault(row['model_id'], []).append(dict(row))
    for item in candidates:
        decision = current.get(item['model_id'])
        decision_matches_version = bool(decision and
            decision['repo_id'] == item['repo_id'] and decision['revision'] == item['revision'])
        if decision and not decision_matches_version:
            decision = dict(decision)
            decision['status'] = 'stale'
        item['decision'] = decision
        item['history'] = history.get(item['model_id'], [])
        item['can_admit'] = item['eligible'] and not (
            decision_matches_version and decision and decision['status'] == 'admitted')
        item['can_revoke'] = bool(decision_matches_version and decision and decision['status'] == 'admitted')
    return candidates


def apply_decision(connection: sqlite3.Connection, *, model_id: int, action: str,
                   confirm: bool, reason: str = '', actor: str = ACTOR,
                   allowlist=None, manifest=None):
    if confirm is not True:
        raise ValueError('Wymagane jawne potwierdzenie decyzji.')
    if action not in {'admit', 'revoke'}:
        raise ValueError('Nieznana operacja decyzji.')
    if actor != ACTOR:
        raise ValueError('Nieprawidłowy aktor audytu.')
    candidates = {item['model_id']: item for item in evaluate_candidates(
        connection=connection, allowlist=allowlist, manifest=manifest)}
    candidate = candidates.get(model_id)
    if candidate is None:
        raise ValueError('Model nie znajduje się na jawnej allowliście.')
    current = connection.execute(
        'SELECT status,repo_id,revision FROM model_admission_decisions WHERE model_id=?', (model_id,)
    ).fetchone()
    if action == 'admit':
        if candidate['blockers']:
            raise ValueError('Nie można dopuścić modelu: ' + ' '.join(candidate['blockers']))
        if (current and current['status'] == 'admitted' and
                current['repo_id'] == candidate['repo_id'] and current['revision'] == candidate['revision']):
            raise ValueError('Model jest już dopuszczony do rejestru dla tej rewizji.')
        status, event = 'admitted', 'admitted'
    else:
        if (not current or current['status'] != 'admitted' or
                current['repo_id'] != candidate['repo_id'] or current['revision'] != candidate['revision']):
            raise ValueError('Model nie ma aktywnej decyzji dopuszczenia dla tej dokładnej rewizji.')
        if not isinstance(reason, str) or len(reason.strip()) < 3:
            raise ValueError('Podaj uzasadnienie wycofania (co najmniej 3 znaki).')
        status, event = 'revoked', 'revoked'
    now = _now()
    snapshot = json.dumps(candidate, ensure_ascii=False, sort_keys=True)
    connection.execute('''INSERT INTO model_admission_decisions
        (model_id,status,actor,decided_at,reason,repo_id,revision,snapshot_json)
        VALUES(?,?,?,?,?,?,?,?)
        ON CONFLICT(model_id) DO UPDATE SET status=excluded.status,actor=excluded.actor,
        decided_at=excluded.decided_at,reason=excluded.reason,repo_id=excluded.repo_id,
        revision=excluded.revision,snapshot_json=excluded.snapshot_json''',
        (model_id, status, actor, now, reason.strip(), candidate['repo_id'], candidate['revision'], snapshot))
    connection.execute('''INSERT INTO model_admission_history
        (model_id,event,actor,occurred_at,reason,repo_id,revision,snapshot_json)
        VALUES(?,?,?,?,?,?,?,?)''',
        (model_id, event, actor, now, reason.strip(), candidate['repo_id'], candidate['revision'], snapshot))
    return {'model_id': model_id, 'status': status, 'actor': actor, 'decided_at': now,
            'reason': reason.strip(), 'repo_id': candidate['repo_id'], 'revision': candidate['revision']}
