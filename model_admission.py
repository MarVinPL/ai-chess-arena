"""Local, auditable model-registry admission decisions.

This registry is deliberately separate from the versioned tournament allowlist:
its decisions never make a model tournament-ready or start model execution.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import model_allowlist
from model_manifest import load_manifest

ACTOR = 'arena'
ELIGIBLE_LICENSE_STATES = {'verified_permissive', 'verified_restricted'}


def _now() -> str:
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def evaluate_candidates(*, allowlist=None, manifest=None):
    """Return allowlist entries with fail-closed admission blockers."""
    allowlist = allowlist if allowlist is not None else model_allowlist.load_allowlist()
    manifest = manifest if manifest is not None else load_manifest()
    manifest_by_id = {item['model_id']: item for item in manifest['models']}
    candidates = []
    for entry in allowlist['models']:
        source = manifest_by_id.get(entry['model_id'])
        blockers = []
        if source is None:
            blockers.append('Brak wpisu w kompletnym manifeście modeli.')
        else:
            if source.get('repo_id') != entry.get('repo_id') or source.get('revision') != entry.get('revision'):
                blockers.append('Repozytorium lub rewizja nie zgadza się z manifestem.')
            if not all(source.get(key) for key in ('filename', 'sha256', 'size_bytes')):
                blockers.append('Manifest nie zawiera kompletnego targetu GGUF.')
            if source.get('license_review_status') not in ELIGIBLE_LICENSE_STATES:
                blockers.append('Audyt licencji nie ma pozytywnego statusu.')
            if source.get('license_review_status') != (entry.get('license_requirements') or {}).get('manifest_audit_status'):
                blockers.append('Status audytu licencji nie zgadza się z allowlistą.')
        if entry.get('status') not in {'eligible', 'restricted'}:
            blockers.append('Wpis allowlisty nie ma statusu eligible ani restricted.')
        if not entry.get('admission_scope'):
            blockers.append('Nie określono jawnego zakresu dopuszczenia.')
        license_info = entry.get('license_requirements') or {}
        if license_info.get('license_verified_required') is not True:
            blockers.append('Brak wymogu niezależnego zatwierdzenia licencji.')
        if not isinstance(license_info.get('manifest_license'), str) or not license_info['manifest_license']:
            blockers.append('Brak identyfikatora licencji w allowliście.')
        if entry.get('technical_readiness') != 'ready' or entry.get('technical_blockers'):
            blockers.append('Gotowość techniczna nie jest pozytywna lub istnieją blokady.')
        if entry.get('block_reason'):
            blockers.append('Wpis zawiera blokadę lub nierozstrzygnięte uzasadnienie blokujące.')
        candidates.append({
            'model_id': entry['model_id'], 'repo_id': entry['repo_id'],
            'revision': entry['revision'], 'status': entry['status'],
            'admission_scope': entry.get('admission_scope', []),
            'license': license_info.get('manifest_license'),
            'technical_readiness': entry.get('technical_readiness'),
            'blockers': blockers, 'eligible': not blockers,
            'display_name': (source or {}).get('repo_id', entry['repo_id']).rsplit('/', 1)[-1],
        })
    return candidates


def list_decisions(connection: sqlite3.Connection, *, allowlist=None, manifest=None):
    candidates = evaluate_candidates(allowlist=allowlist, manifest=manifest)
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
        allowlist=allowlist, manifest=manifest)}
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
