"""Auditable user-submitted review proposals; never changes allowlist or admission."""
import json
import time


def _now():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())

def submit(connection, payload, *, allowed_model_ids):
    model_id = int(payload.get('model_id', 0))
    if model_id not in set(allowed_model_ids):
        raise ValueError('Model nie jest aktywnym kandydatem w allowliście; wykluczone modele są niedostępne.')
    fields = {
        'license_id': str(payload.get('license_id', '')).strip()[:200],
        'license_review': str(payload.get('license_review', 'pending')).strip(),
        'license_notes': str(payload.get('license_notes', '')).strip()[:5000],
        'technical_readiness': str(payload.get('technical_readiness', 'not_evaluated')).strip(),
        'technical_evidence': str(payload.get('technical_evidence', '')).strip()[:5000],
        'technical_blockers': str(payload.get('technical_blockers', '')).strip()[:3000],
        'revision_confirmed': payload.get('revision_confirmed') is True,
        'revision_notes': str(payload.get('revision_notes', '')).strip()[:2000],
        'proposed_status': str(payload.get('proposed_status', 'review_required')).strip(),
        'restrictions': str(payload.get('restrictions', '')).strip()[:3000],
        'obligations': str(payload.get('obligations', '')).strip()[:3000],
        'block_reason': str(payload.get('block_reason', '')).strip()[:3000],
        'scope_formats': payload.get('scope_formats', []) if isinstance(payload.get('scope_formats', []), list) else None,
    }
    if fields['license_review'] not in {'pending', 'verified_permissive', 'verified_restricted', 'not_compatible'}:
        raise ValueError('Nieprawidłowy status przeglądu licencji.')
    if fields['proposed_status'] not in {'review_required', 'blocked', 'eligible', 'restricted'}:
        raise ValueError('Nieprawidłowy proponowany status allowlisty.')
    if fields['scope_formats'] is None or not all(isinstance(value, str) for value in fields['scope_formats']) or not set(fields['scope_formats']).issubset({'PURE_AI', 'AI_ENGINE'}):
        raise ValueError('Nieprawidłowy zakres turniejowy.')
    fields['scope_formats'] = sorted(set(fields['scope_formats']))
    if fields['proposed_status'] in {'eligible', 'restricted'} and not fields['scope_formats']:
        raise ValueError('Pozytywna propozycja wymaga zakresu PURE_AI lub AI_ENGINE.')
    if fields['proposed_status'] in {'eligible', 'restricted'} and not fields['revision_confirmed']:
        raise ValueError('Pozytywna propozycja wymaga potwierdzenia dokładnej rewizji.')
    if fields['proposed_status'] == 'eligible' and fields['license_review'] != 'verified_permissive':
        raise ValueError('eligible wymaga audytu verified_permissive.')
    if fields['proposed_status'] == 'restricted' and fields['license_review'] != 'verified_restricted':
        raise ValueError('restricted wymaga audytu verified_restricted.')
    if fields['proposed_status'] in {'blocked', 'review_required'} and fields['scope_formats']:
        raise ValueError('Status blokujący nie może zawierać zakresu dopuszczenia.')
    if fields['proposed_status'] in {'eligible', 'restricted'} and fields['block_reason']:
        raise ValueError('Pozytywna propozycja nie może zawierać nierozstrzygniętej blokady.')
    if fields['proposed_status'] == 'restricted' and (not fields['restrictions'] or not fields['obligations']):
        raise ValueError('Status restricted wymaga opisania ograniczeń i obowiązków licencyjnych.')
    if fields['proposed_status'] in {'blocked', 'review_required'} and not fields['block_reason']:
        raise ValueError('Status blokujący wymaga uzasadnienia.')
    if fields['technical_readiness'] not in {'not_evaluated', 'ready', 'blocked'}:
        raise ValueError('Nieprawidłowy status gotowości technicznej.')
    if fields['license_review'] != 'pending' and not fields['license_id']:
        raise ValueError('Wpisz identyfikator licencji, aby zapisać jej ocenę.')
    if fields['license_review'] != 'pending' and not fields['license_notes']:
        raise ValueError('Dodaj źródła i notatki potwierdzające ocenę licencji.')
    if fields['technical_readiness'] == 'ready' and not fields['technical_evidence']:
        raise ValueError('Do propozycji gotowości READY wymagany jest opis dowodów testu.')
    if fields['technical_readiness'] == 'ready' and not fields['revision_confirmed']:
        raise ValueError('Propozycja READY wymaga potwierdzenia dokładnej rewizji.')
    if fields['technical_readiness'] == 'ready' and fields['technical_blockers']:
        raise ValueError('Gotowość READY nie może zawierać nierozwiązanych blokad technicznych.')
    if fields['technical_readiness'] == 'blocked' and not fields['technical_blockers']:
        raise ValueError('Opisz blokady wykryte w teście technicznym.')
    now = _now()
    connection.execute('''CREATE TABLE IF NOT EXISTS model_review_submissions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, model_id INTEGER NOT NULL CHECK(model_id > 0),
        submitted_at TEXT NOT NULL, payload_json TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending_review' CHECK(status IN ('pending_review','accepted_into_pr','rejected'))
    )''')
    cur = connection.execute('INSERT INTO model_review_submissions(model_id,submitted_at,payload_json) VALUES(?,?,?)',
                              (model_id, now, json.dumps(fields, ensure_ascii=False, sort_keys=True)))
    return {'submission_id': cur.lastrowid, 'model_id': model_id, 'submitted_at': now,
            'status': 'pending_review', 'proposal': fields,
            'note': 'Zapisano propozycję do audytu. Nie zmieniono allowlisty, licencji ani gotowości modelu.'}


def _ensure_review_events(connection):
    connection.execute('''CREATE TABLE IF NOT EXISTS model_review_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        submission_id INTEGER NOT NULL,
        event TEXT NOT NULL,
        actor TEXT NOT NULL,
        occurred_at TEXT NOT NULL,
        reason TEXT NOT NULL DEFAULT '',
        previous_status TEXT,
        new_status TEXT,
        previous_payload_json TEXT,
        new_payload_json TEXT
    )''')


def list_submissions(connection):
    _ensure_review_events(connection)
    connection.row_factory = __import__('sqlite3').Row
    rows = connection.execute('''SELECT id,model_id,submitted_at,payload_json,status
        FROM model_review_submissions ORDER BY
        CASE status WHEN 'pending_review' THEN 0 WHEN 'accepted_into_pr' THEN 1 ELSE 2 END,
        id DESC''').fetchall()
    result = []
    for row in rows:
        item = dict(row)
        try:
            item['proposal'] = json.loads(item.pop('payload_json'))
        except (TypeError, json.JSONDecodeError):
            item['proposal'] = {}
        events = connection.execute('''SELECT event,actor,occurred_at,reason,previous_status,new_status
            FROM model_review_events WHERE submission_id=? ORDER BY id''', (item['id'],)).fetchall()
        item['history'] = [dict(event) for event in events]
        result.append(item)
    return result


def apply_action(connection, *, submission_id, action, actor='arena', reason='', payload=None,
                 allowed_model_ids=()):
    _ensure_review_events(connection)
    row = connection.execute('''SELECT id,model_id,submitted_at,payload_json,status
        FROM model_review_submissions WHERE id=?''', (int(submission_id),)).fetchone()
    if row is None:
        raise ValueError('Nie znaleziono propozycji audytowej.')
    # SELECT order: id, model_id, submitted_at, payload_json, status.
    # Preserve the exact prior JSON in the append-only event log.
    previous_payload_json = row[3]
    previous_status = row[4]
    now = _now()
    if action == 'accept':
        if previous_status != 'pending_review':
            raise ValueError('Akceptować można wyłącznie propozycję oczekującą na przegląd.')
        if str(reason).strip():
            reason = str(reason).strip()[:2000]
        new_status = 'accepted_into_pr'
        event = 'accepted_into_pr'
        new_payload_json = previous_payload_json
    elif action == 'reject':
        reason = str(reason).strip()[:2000]
        if len(reason) < 3:
            raise ValueError('Odrzucenie wymaga uzasadnienia (minimum 3 znaki).')
        if previous_status == 'rejected':
            raise ValueError('Propozycja jest już odrzucona.')
        new_status = 'rejected'
        event = 'rejected'
        new_payload_json = previous_payload_json
    elif action == 'revise':
        if not isinstance(payload, dict):
            raise ValueError('Brak poprawionej treści propozycji.')
        # Reuse the exact submission validator without creating a production row.
        import sqlite3
        temporary = sqlite3.connect(':memory:')
        try:
            submit_result = submit(temporary, payload, allowed_model_ids=allowed_model_ids)
            new_payload_json = temporary.execute('SELECT payload_json FROM model_review_submissions WHERE id=?',
                                                 (submit_result['submission_id'],)).fetchone()[0]
        finally:
            temporary.close()
        new_status = 'pending_review'
        event = 'revised'
        reason = str(reason).strip()[:2000]
    else:
        raise ValueError('Nieznana akcja przeglądu.')
    connection.execute('UPDATE model_review_submissions SET payload_json=?,status=? WHERE id=?',
                       (new_payload_json, new_status, int(submission_id)))
    connection.execute('''INSERT INTO model_review_events
        (submission_id,event,actor,occurred_at,reason,previous_status,new_status,previous_payload_json,new_payload_json)
        VALUES(?,?,?,?,?,?,?,?,?)''', (int(submission_id), event, str(actor)[:120], now, reason,
        previous_status, new_status, previous_payload_json, new_payload_json))
    return {'submission_id': int(submission_id), 'status': new_status, 'event': event,
            'note': 'Zapisano decyzję przeglądu. Allowlista i lokalne dopuszczenie pozostają bez zmian.'}
