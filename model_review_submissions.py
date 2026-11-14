"""Auditable user-submitted review proposals; never changes allowlist or admission."""
import json
import time

MODEL_IDS = {1, 2, 3}

def _now():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())

def submit(connection, payload):
    model_id = int(payload.get('model_id', 0))
    if model_id not in MODEL_IDS:
        raise ValueError('Formularz przyjmuje wyłącznie modele 1–3.')
    fields = {
        'license_id': str(payload.get('license_id', '')).strip()[:200],
        'license_review': str(payload.get('license_review', 'pending')).strip(),
        'license_notes': str(payload.get('license_notes', '')).strip()[:5000],
        'admission_scope': str(payload.get('admission_scope', '')).strip(),
        'technical_readiness': str(payload.get('technical_readiness', 'not_evaluated')).strip(),
        'technical_evidence': str(payload.get('technical_evidence', '')).strip()[:5000],
        'revision_confirmed': payload.get('revision_confirmed') is True,
        'revision_notes': str(payload.get('revision_notes', '')).strip()[:2000],
    }
    if fields['license_review'] not in {'pending', 'verified_permissive', 'verified_restricted', 'not_compatible'}:
        raise ValueError('Nieprawidłowy status przeglądu licencji.')
    if fields['admission_scope'] not in {'', 'arena', 'tournament', 'arena,tournament'}:
        raise ValueError('Nieprawidłowy zakres propozycji.')
    if fields['technical_readiness'] not in {'not_evaluated', 'ready', 'blocked'}:
        raise ValueError('Nieprawidłowy status gotowości technicznej.')
    if fields['license_review'] != 'pending' and not fields['license_id']:
        raise ValueError('Wpisz identyfikator licencji, aby zapisać jej ocenę.')
    if fields['technical_readiness'] == 'ready' and not fields['technical_evidence']:
        raise ValueError('Do propozycji gotowości READY wymagany jest opis dowodów testu.')
    now = _now()
    connection.execute('''CREATE TABLE IF NOT EXISTS model_review_submissions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, model_id INTEGER NOT NULL CHECK(model_id IN (1,2,3)),
        submitted_at TEXT NOT NULL, payload_json TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending_review' CHECK(status IN ('pending_review','accepted_into_pr','rejected'))
    )''')
    cur = connection.execute('INSERT INTO model_review_submissions(model_id,submitted_at,payload_json) VALUES(?,?,?)',
                              (model_id, now, json.dumps(fields, ensure_ascii=False, sort_keys=True)))
    return {'submission_id': cur.lastrowid, 'model_id': model_id, 'submitted_at': now,
            'status': 'pending_review', 'proposal': fields,
            'note': 'Zapisano propozycję do audytu. Nie zmieniono allowlisty, licencji ani gotowości modelu.'}
