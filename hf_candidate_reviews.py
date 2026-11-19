"""Manual, revision-pinned review notes for Hugging Face candidates.

These records are evidence/review workflow only: they never admit a model,
change an allowlist, download weights, or execute model code.
"""
import json
import time

LICENSE_STATES = {'pending', 'declared_permissive', 'verified_permissive', 'verified_restricted', 'not_compatible'}
TECHNICAL_STATES = {'not_evaluated', 'ready', 'blocked'}


def _now():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def _candidate(catalog_id, revision, catalog=None):
    from hf_candidate_audit import load_catalog
    items = catalog if catalog is not None else load_catalog()
    matches = [item for item in items if item.get('catalog_id') == catalog_id]
    if len(matches) != 1:
        raise ValueError('Kandydat nie istnieje w aktualnym katalogu lub wpis jest niejednoznaczny.')
    item = matches[0]
    pinned = item.get('revision') or item.get('audited_revision')
    if not isinstance(revision, str) or len(revision) != 40 or revision != pinned:
        raise ValueError('Rewizja nie jest aktualną, przypiętą rewizją kandydata.')
    return item


def save(connection, payload, *, catalog=None, actor='arena'):
    if not isinstance(payload, dict):
        raise ValueError('Nieprawidłowy formularz weryfikacji.')
    catalog_id = payload.get('catalog_id')
    revision = payload.get('revision')
    if not isinstance(catalog_id, str) or not catalog_id or len(catalog_id) > 256:
        raise ValueError('Brak prawidłowego identyfikatora kandydata.')
    item = _candidate(catalog_id, revision, catalog)
    fields = {
        'catalog_id': catalog_id,
        'repo_id': item.get('repo_id'),
        'display_name': item.get('display_name'),
        'revision': revision,
        'license_id': str(payload.get('license_id', '')).strip()[:200],
        'license_status': str(payload.get('license_status', 'pending')).strip(),
        'license_evidence': str(payload.get('license_evidence', '')).strip()[:6000],
        'revision_confirmed': payload.get('revision_confirmed') is True,
        'revision_notes': str(payload.get('revision_notes', '')).strip()[:3000],
        'technical_readiness': str(payload.get('technical_readiness', 'not_evaluated')).strip(),
        'technical_evidence': str(payload.get('technical_evidence', '')).strip()[:6000],
        'technical_blockers': str(payload.get('technical_blockers', '')).strip()[:4000],
        'reviewer_notes': str(payload.get('reviewer_notes', '')).strip()[:4000],
    }
    if fields['license_status'] not in LICENSE_STATES:
        raise ValueError('Nieprawidłowa ocena licencji.')
    if fields['technical_readiness'] not in TECHNICAL_STATES:
        raise ValueError('Nieprawidłowa ocena techniczna.')
    if fields['license_status'] != 'pending' and (not fields['license_id'] or len(fields['license_evidence']) < 10):
        raise ValueError('Ocena licencji wymaga identyfikatora oraz źródeł i uzasadnienia.')
    if fields['revision_confirmed'] and len(fields['revision_notes']) < 5:
        raise ValueError('Potwierdzenie rewizji wymaga notatki z dowodem porównania SHA.')
    if fields['technical_readiness'] == 'ready':
        if len(fields['technical_evidence']) < 10:
            raise ValueError('Ocena READY wymaga dowodów testu technicznego.')
        if not fields['revision_confirmed']:
            raise ValueError('Ocena READY wymaga potwierdzenia przypiętej rewizji.')
        if fields['technical_blockers']:
            raise ValueError('Ocena READY nie może zawierać nierozwiązanych blokad.')
    if fields['technical_readiness'] == 'blocked' and len(fields['technical_blockers']) < 5:
        raise ValueError('Ocena BLOCKED wymaga opisu blokad technicznych.')

    now = _now()
    connection.execute('''CREATE TABLE IF NOT EXISTS hf_candidate_reviews (
        catalog_id TEXT NOT NULL, revision TEXT NOT NULL, reviewed_at TEXT NOT NULL,
        reviewer TEXT NOT NULL, review_json TEXT NOT NULL,
        PRIMARY KEY(catalog_id, revision)
    )''')
    connection.execute('''CREATE TABLE IF NOT EXISTS hf_candidate_review_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT, catalog_id TEXT NOT NULL, revision TEXT NOT NULL,
        occurred_at TEXT NOT NULL, actor TEXT NOT NULL, previous_json TEXT, new_json TEXT NOT NULL
    )''')
    previous = connection.execute('SELECT review_json FROM hf_candidate_reviews WHERE catalog_id=? AND revision=?',
                                  (catalog_id, revision)).fetchone()
    encoded = json.dumps(fields, ensure_ascii=False, sort_keys=True)
    connection.execute('''INSERT INTO hf_candidate_reviews(catalog_id,revision,reviewed_at,reviewer,review_json)
        VALUES(?,?,?,?,?) ON CONFLICT(catalog_id,revision) DO UPDATE SET
        reviewed_at=excluded.reviewed_at, reviewer=excluded.reviewer, review_json=excluded.review_json''',
        (catalog_id, revision, now, str(actor)[:120], encoded))
    connection.execute('''INSERT INTO hf_candidate_review_events
        (catalog_id,revision,occurred_at,actor,previous_json,new_json) VALUES(?,?,?,?,?,?)''',
        (catalog_id, revision, now, str(actor)[:120], previous[0] if previous else None, encoded))
    _ensure_proposal_tables(connection)
    proposal = connection.execute('SELECT id,status FROM hf_candidate_review_proposals WHERE catalog_id=? AND revision=?',
                                  (catalog_id, revision)).fetchone()
    if proposal is None:
        cur = connection.execute('''INSERT INTO hf_candidate_review_proposals
            (catalog_id,revision,repo_id,submitted_at,status) VALUES(?,?,?,?,?)''',
            (catalog_id, revision, str(item.get('repo_id') or ''), now, 'pending_review'))
        _proposal_event(connection, cur.lastrowid, 'submitted', actor, '', None, 'pending_review')
    else:
        connection.execute("UPDATE hf_candidate_review_proposals SET submitted_at=?,status='pending_review' WHERE id=?",
                           (now, proposal[0]))
        _proposal_event(connection, proposal[0], 'resubmitted', actor, 'Zaktualizowano dowody; ponowny przegląd wymagany.', proposal[1], 'pending_review')
    return {'review': fields, 'reviewed_at': now, 'reviewer': str(actor)[:120],
            'workflow_status': 'review_in_progress',
            'note': 'Zapisano ręczne ustalenia. To nie jest zatwierdzenie licencji ani dopuszczenie modelu do Areny.'}


def get(connection, catalog_id, revision):
    try:
        row = connection.execute('SELECT reviewed_at,reviewer,review_json FROM hf_candidate_reviews WHERE catalog_id=? AND revision=?',
                                 (catalog_id, revision)).fetchone()
    except Exception:
        return None
    if not row:
        return None
    try:
        events = connection.execute('''SELECT occurred_at,actor,previous_json,new_json
            FROM hf_candidate_review_events WHERE catalog_id=? AND revision=? ORDER BY id''',
            (catalog_id, revision)).fetchall()
    except Exception:
        events = []
    _ensure_proposal_tables(connection)
    proposal = connection.execute('SELECT id,status,submitted_at FROM hf_candidate_review_proposals WHERE catalog_id=? AND revision=?',
                                   (catalog_id, revision)).fetchone()
    return {'reviewed_at': row[0], 'reviewer': row[1], 'review': json.loads(row[2]),
            'proposal_id': proposal[0] if proposal else None,
            'proposal_status': proposal[1] if proposal else 'not_submitted',
            'workflow_status': 'review_in_progress',
            'history': [{'occurred_at': event[0], 'actor': event[1],
                         'previous_review': json.loads(event[2]) if event[2] else None,
                         'review': json.loads(event[3])} for event in events]}


def list_reviews(connection):
    try:
        rows = connection.execute('SELECT catalog_id,revision,reviewed_at,reviewer FROM hf_candidate_reviews').fetchall()
    except Exception:
        return []
    return [{'catalog_id': row[0], 'revision': row[1], 'reviewed_at': row[2], 'reviewer': row[3]}
            for row in rows]


def _ensure_proposal_tables(connection):
    connection.execute('''CREATE TABLE IF NOT EXISTS hf_candidate_reviews (
        catalog_id TEXT NOT NULL, revision TEXT NOT NULL, reviewed_at TEXT NOT NULL,
        reviewer TEXT NOT NULL, review_json TEXT NOT NULL,
        PRIMARY KEY(catalog_id, revision)
    )''')
    connection.execute('''CREATE TABLE IF NOT EXISTS hf_candidate_review_proposals (
        id INTEGER PRIMARY KEY AUTOINCREMENT, catalog_id TEXT NOT NULL, revision TEXT NOT NULL,
        repo_id TEXT NOT NULL, submitted_at TEXT NOT NULL, status TEXT NOT NULL
            CHECK(status IN ('pending_review','accepted_into_pr','rejected')),
        UNIQUE(catalog_id, revision)
    )''')
    connection.execute('''CREATE TABLE IF NOT EXISTS hf_candidate_review_proposal_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT, proposal_id INTEGER NOT NULL, event TEXT NOT NULL,
        actor TEXT NOT NULL, occurred_at TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '',
        previous_status TEXT, new_status TEXT
    )''')

def _proposal_event(connection, proposal_id, event, actor, reason, previous, new):
    connection.execute('''INSERT INTO hf_candidate_review_proposal_events
        (proposal_id,event,actor,occurred_at,reason,previous_status,new_status)
        VALUES(?,?,?,?,?,?,?)''', (proposal_id, event, str(actor)[:120], _now(),
        str(reason)[:2000], previous, new))

def list_proposals(connection):
    _ensure_proposal_tables(connection)
    rows = connection.execute('''SELECT p.id,p.catalog_id,p.revision,p.repo_id,p.submitted_at,p.status,
        r.review_json FROM hf_candidate_review_proposals p
        JOIN hf_candidate_reviews r ON r.catalog_id=p.catalog_id AND r.revision=p.revision
        ORDER BY CASE p.status WHEN 'pending_review' THEN 0 WHEN 'accepted_into_pr' THEN 1 ELSE 2 END,p.id DESC''').fetchall()
    result = []
    for row in rows:
        item = {'id': row[0], 'catalog_id': row[1], 'revision': row[2], 'repo_id': row[3],
                'submitted_at': row[4], 'status': row[5], 'review': json.loads(row[6])}
        events = connection.execute('''SELECT event,actor,occurred_at,reason,previous_status,new_status
            FROM hf_candidate_review_proposal_events WHERE proposal_id=? ORDER BY id''', (item['id'],)).fetchall()
        item['history'] = [{'event': e[0], 'actor': e[1], 'occurred_at': e[2],
                            'reason': e[3], 'previous_status': e[4], 'new_status': e[5]} for e in events]
        result.append(item)
    return result

def apply_proposal_action(connection, *, proposal_id, action, catalog=None, actor='arena', reason=''):
    _ensure_proposal_tables(connection)
    row = connection.execute('''SELECT id,catalog_id,revision,repo_id,status
        FROM hf_candidate_review_proposals WHERE id=?''', (int(proposal_id),)).fetchone()
    if row is None:
        raise ValueError('Nie znaleziono propozycji kandydata.')
    _candidate(row[1], row[2], catalog)  # revalidate exact current catalog revision at decision time
    previous = row[4]
    if previous != 'pending_review':
        raise ValueError('Decyzję można wykonać wyłącznie dla propozycji oczekującej na przegląd.')
    if action == 'accept':
        new, event, reason = 'accepted_into_pr', 'accepted_into_pr', str(reason).strip()[:2000]
    elif action == 'reject':
        reason = str(reason).strip()[:2000]
        if len(reason) < 3:
            raise ValueError('Odrzucenie wymaga uzasadnienia (minimum 3 znaki).')
        new, event = 'rejected', 'rejected'
    else:
        raise ValueError('Nieznana akcja propozycji kandydata.')
    connection.execute('UPDATE hf_candidate_review_proposals SET status=? WHERE id=?', (new, row[0]))
    _proposal_event(connection, row[0], event, actor, reason, previous, new)
    return {'proposal_id': row[0], 'catalog_id': row[1], 'revision': row[2], 'status': new,
            'note': 'Zapisano decyzję workflow. To wyłącznie etap przeglądu/PR; bez admission, allowlisty, pobierania, runtime i rosteru.'}
