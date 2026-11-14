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
        'scope_formats': sorted(set(payload.get('scope_formats', []))) if isinstance(payload.get('scope_formats', []), list) else [],
    }
    if fields['license_review'] not in {'pending', 'verified_permissive', 'verified_restricted', 'not_compatible'}:
        raise ValueError('Nieprawidłowy status przeglądu licencji.')
    if fields['proposed_status'] not in {'review_required', 'blocked', 'eligible', 'restricted'}:
        raise ValueError('Nieprawidłowy proponowany status allowlisty.')
    if not all(isinstance(value, str) for value in fields['scope_formats']) or not set(fields['scope_formats']).issubset({'PURE_AI', 'AI_ENGINE'}):
        raise ValueError('Nieprawidłowy zakres turniejowy.')
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
