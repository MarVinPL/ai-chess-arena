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
    ensure_schema(connection)
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
    ensure_schema(connection)
    _ensure_review_events(connection)
    connection.row_factory = __import__('sqlite3').Row
    rows = connection.execute('''SELECT id,model_id,submitted_at,payload_json,status
        FROM model_review_submissions WHERE model_id IS NOT NULL ORDER BY
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
    ensure_schema(connection)
    _ensure_review_events(connection)
    row = connection.execute('''SELECT id,model_id,submitted_at,payload_json,status
        FROM model_review_submissions WHERE id=?''', (int(submission_id),)).fetchone()
    if row is None:
        raise ValueError('Nie znaleziono propozycji audytowej.')
    if row[1] is None:
        raise ValueError('Propozycję HF obsługuje wyłącznie akcja z ponowną walidacją katalogu.')
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


def ensure_schema(connection):
    """Ensure the Arena and HF review workflows share one identity-safe queue."""
    connection.execute("SAVEPOINT review_queue_schema")
    try:
        exists = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='model_review_submissions'").fetchone()
        if not exists:
            connection.execute("CREATE TABLE model_review_submissions (id INTEGER PRIMARY KEY AUTOINCREMENT, model_id INTEGER, catalog_id TEXT, revision TEXT, repo_id TEXT, submitted_at TEXT NOT NULL, payload_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending_review' CHECK(status IN ('pending_review','accepted_into_pr','rejected')), CHECK ((model_id IS NOT NULL AND model_id>0 AND catalog_id IS NULL AND revision IS NULL AND repo_id IS NULL) OR (model_id IS NULL AND length(catalog_id)>0 AND length(revision)=40 AND length(repo_id)>0)))")
        else:
            info = list(connection.execute('PRAGMA table_info(model_review_submissions)'))
            cols = {r[1] for r in info}
            model_not_null = any(r[1] == 'model_id' and r[3] for r in info)
            if not {'catalog_id','revision','repo_id'}.issubset(cols) or model_not_null:
                connection.execute('ALTER TABLE model_review_submissions RENAME TO model_review_submissions_legacy')
                connection.execute("CREATE TABLE model_review_submissions (id INTEGER PRIMARY KEY AUTOINCREMENT, model_id INTEGER, catalog_id TEXT, revision TEXT, repo_id TEXT, submitted_at TEXT NOT NULL, payload_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending_review' CHECK(status IN ('pending_review','accepted_into_pr','rejected')), CHECK ((model_id IS NOT NULL AND model_id>0 AND catalog_id IS NULL AND revision IS NULL AND repo_id IS NULL) OR (model_id IS NULL AND length(catalog_id)>0 AND length(revision)=40 AND length(repo_id)>0)))")
                connection.execute('INSERT INTO model_review_submissions(id,model_id,submitted_at,payload_json,status) SELECT id,model_id,submitted_at,payload_json,status FROM model_review_submissions_legacy')
                connection.execute('DROP TABLE model_review_submissions_legacy')
        _ensure_review_events(connection)
        legacy = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='hf_candidate_review_proposals'").fetchone()
        if legacy:
            rows = connection.execute('SELECT id,catalog_id,revision,repo_id,submitted_at,status FROM hf_candidate_review_proposals ORDER BY id').fetchall()
            for old_id,catalog_id,revision,repo_id,submitted_at,status in rows:
                found = connection.execute('SELECT id FROM model_review_submissions WHERE model_id IS NULL AND catalog_id=? AND revision=? AND repo_id=?', (catalog_id,revision,repo_id)).fetchone()
                if found:
                    new_id = found[0]
                else:
                    review = connection.execute('SELECT review_json FROM hf_candidate_reviews WHERE catalog_id=? AND revision=?', (catalog_id,revision)).fetchone()
                    payload_json = review[0] if review else '{}'
                    id_conflict = connection.execute('SELECT 1 FROM model_review_submissions WHERE id=?', (old_id,)).fetchone()
                    if id_conflict:
                        cur = connection.execute('INSERT INTO model_review_submissions(model_id,catalog_id,revision,repo_id,submitted_at,payload_json,status) VALUES(NULL,?,?,?,?,?,?)', (catalog_id,revision,repo_id,submitted_at,payload_json,status))
                    else:
                        cur = connection.execute('INSERT INTO model_review_submissions(id,model_id,catalog_id,revision,repo_id,submitted_at,payload_json,status) VALUES(?,NULL,?,?,?,?,?,?)', (old_id,catalog_id,revision,repo_id,submitted_at,payload_json,status))
                    new_id = cur.lastrowid
                old_events = connection.execute('SELECT id,event,actor,occurred_at,reason,previous_status,new_status FROM hf_candidate_review_proposal_events WHERE proposal_id=? ORDER BY id', (old_id,)).fetchall()
                for old_event_id,event,actor,occurred_at,reason,previous_status,new_status in old_events:
                    if not connection.execute('SELECT 1 FROM model_review_events WHERE submission_id=? AND event=? AND actor=? AND occurred_at=?', (new_id,event,actor,occurred_at)).fetchone():
                        event_conflict = connection.execute('SELECT 1 FROM model_review_events WHERE id=?', (old_event_id,)).fetchone()
                        if event_conflict:
                            connection.execute('INSERT INTO model_review_events(submission_id,event,actor,occurred_at,reason,previous_status,new_status) VALUES(?,?,?,?,?,?,?)', (new_id,event,actor,occurred_at,reason,previous_status,new_status))
                        else:
                            connection.execute('INSERT INTO model_review_events(id,submission_id,event,actor,occurred_at,reason,previous_status,new_status) VALUES(?,?,?,?,?,?,?,?)', (old_event_id,new_id,event,actor,occurred_at,reason,previous_status,new_status))
            connection.execute('DROP TABLE hf_candidate_review_proposal_events')
            connection.execute('DROP TABLE hf_candidate_review_proposals')
        connection.execute('RELEASE SAVEPOINT review_queue_schema')
    except Exception:
        connection.execute('ROLLBACK TO SAVEPOINT review_queue_schema')
        connection.execute('RELEASE SAVEPOINT review_queue_schema')
        raise


def submit_hf_candidate(connection, *, catalog_id, revision, repo_id, submitted_at, payload_json, actor='arena'):
    ensure_schema(connection)
    row = connection.execute('SELECT id,status FROM model_review_submissions WHERE model_id IS NULL AND catalog_id=? AND revision=? AND repo_id=?', (catalog_id,revision,repo_id)).fetchone()
    if row:
        connection.execute("UPDATE model_review_submissions SET submitted_at=?,payload_json=?,status='pending_review' WHERE id=?", (submitted_at,payload_json,row[0]))
        previous = row[1]
        event = 'resubmitted'
        submission_id = row[0]
    else:
        cur = connection.execute("INSERT INTO model_review_submissions(model_id,catalog_id,revision,repo_id,submitted_at,payload_json,status) VALUES(NULL,?,?,?,?,?,'pending_review')", (catalog_id,revision,repo_id,submitted_at,payload_json))
        submission_id = cur.lastrowid
        previous = None
        event = 'submitted'
    connection.execute('INSERT INTO model_review_events(submission_id,event,actor,occurred_at,reason,previous_status,new_status,previous_payload_json,new_payload_json) VALUES(?,?,?,?,?,?,?,?,?)', (submission_id,event,str(actor)[:120],submitted_at,'Zapisano/odświeżono propozycję HF.',previous,'pending_review',None,payload_json))
    return submission_id


def list_hf_candidates(connection):
    ensure_schema(connection)
    rows = connection.execute("SELECT id,catalog_id,revision,repo_id,submitted_at,status,payload_json FROM model_review_submissions WHERE model_id IS NULL ORDER BY CASE status WHEN 'pending_review' THEN 0 WHEN 'accepted_into_pr' THEN 1 ELSE 2 END,id DESC").fetchall()
    result=[]
    for row in rows:
        item={'id':row[0],'catalog_id':row[1],'revision':row[2],'repo_id':row[3],'submitted_at':row[4],'status':row[5],'review':json.loads(row[6] or '{}')}
        events=connection.execute('SELECT event,actor,occurred_at,reason,previous_status,new_status FROM model_review_events WHERE submission_id=? ORDER BY id',(item['id'],)).fetchall()
        item['history']=[{'event':e[0],'actor':e[1],'occurred_at':e[2],'reason':e[3],'previous_status':e[4],'new_status':e[5]} for e in events]
        result.append(item)
    return result


def apply_hf_candidate_action(connection, *, submission_id, action, catalog=None, actor='arena', reason=''):
    ensure_schema(connection)
    row=connection.execute('SELECT id,catalog_id,revision,repo_id,status,payload_json FROM model_review_submissions WHERE id=? AND model_id IS NULL',(int(submission_id),)).fetchone()
    if not row:
        raise ValueError('Nie znaleziono propozycji kandydata HF.')
    from hf_candidate_reviews import _candidate
    current = _candidate(row[1],row[2],catalog)
    if str(current.get('repo_id') or '') != row[3]:
        raise ValueError('Repozytorium kandydata zmieniło się względem zgłoszenia; decyzja zablokowana.')
    previous=row[4]
    if previous!='pending_review':
        raise ValueError('Decyzję można wykonać wyłącznie dla propozycji oczekującej na przegląd.')
    if action=='accept':
        new='accepted_into_pr'; event='accepted_into_pr'; reason=str(reason).strip()[:2000]
    elif action=='reject':
        reason=str(reason).strip()[:2000]
        if len(reason)<3: raise ValueError('Odrzucenie wymaga uzasadnienia (minimum 3 znaki).')
        new='rejected'; event='rejected'
    else:
        raise ValueError('Nieznana akcja propozycji kandydata.')
    now=_now()
    connection.execute('UPDATE model_review_submissions SET status=? WHERE id=?',(new,row[0]))
    connection.execute('INSERT INTO model_review_events(submission_id,event,actor,occurred_at,reason,previous_status,new_status,previous_payload_json,new_payload_json) VALUES(?,?,?,?,?,?,?,?,?)',(row[0],event,str(actor)[:120],now,reason,previous,new,row[5],row[5]))
    return {'proposal_id':row[0],'submission_id':row[0],'catalog_id':row[1],'revision':row[2],'status':new,'note':'Zapisano decyzję w wspólnej kolejce, bez admission, allowlisty, pobierania, runtime i rosteru.'}
