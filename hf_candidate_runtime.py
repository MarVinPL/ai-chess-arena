"""Preflight and durable runtime-test queue for verified quarantined GGUFs.

This module NEVER starts inference. It only verifies quarantine provenance and
host capacity, then records either a hardware block or readiness for a separate
explicit runtime approval. No artifact is copied, registered, or activated.
"""
from __future__ import annotations
import json, shutil, time
from pathlib import Path
import league
import hf_candidate_downloads
import hf_candidate_queue
import hf_candidate_reviews

# Conservative CPU-memory estimate: mapped weights + runtime overhead/context.
MEMORY_MULTIPLIER = 1.5
MEMORY_RESERVE_BYTES = 1536 * 1024**2
DISK_RESERVE_BYTES = 512 * 1024**2
REFRESH_SECONDS = 15 * 60

# Separate from the download allowlist. This adds a candidate to the runtime
# review/preflight workflow only; it does not authorize downloading or execution.
RUNTIME_CANDIDATES = {
    ('hf:360ZhiNao/Light-R1-14B-DS-GGUF',
     '0d4531e58894e0019d4a9a6aa775eb32098213cb',
     'Light-R1-14B-Q4_0.gguf'),
}


def _now(): return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def _init(c):
    c.execute('''CREATE TABLE IF NOT EXISTS hf_runtime_preflight (
        artifact_key TEXT PRIMARY KEY, catalog_id TEXT NOT NULL, repo_id TEXT NOT NULL,
        revision TEXT NOT NULL, filename TEXT NOT NULL, path TEXT NOT NULL,
        size_bytes INTEGER NOT NULL, sha256 TEXT NOT NULL, status TEXT NOT NULL,
        reason TEXT NOT NULL, estimated_ram_bytes INTEGER, available_ram_bytes INTEGER,
        checked_at TEXT NOT NULL, history_json TEXT NOT NULL DEFAULT '[]')''')


def _memory_available():
    try:
        for line in Path('/proc/meminfo').read_text().splitlines():
            if line.startswith('MemAvailable:'):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return None


def _verified_artifacts():
    with hf_candidate_downloads._db() as c:
        rows=c.execute('SELECT * FROM hf_download_verified_artifacts ORDER BY verified_at').fetchall()
    return [dict(row) for row in rows]


def _review_blockers(connection, catalog_id, revision):
    review_record = hf_candidate_reviews.get(connection, catalog_id, revision)
    if not review_record:
        return ['Brak ręcznego przeglądu licencji i gotowości dla przypiętej rewizji.']
    review = review_record.get('review') or {}
    blockers = []
    if review.get('license_status') not in {'declared_permissive', 'verified_permissive'}:
        blockers.append('Brak pozytywnej oceny licencji (declared_permissive lub verified_permissive).')
    if review.get('license_status') == 'declared_permissive' and not any(token in (review.get('license_evidence') or '').lower() for token in ('apache-2.0', 'apache 2.0')):
        blockers.append('Status declared_permissive wymaga jawnego wskazania deklaracji Apache-2.0 w dowodach.')
    if review.get('revision_confirmed') is not True:
        blockers.append('Przypięta rewizja nie została ręcznie potwierdzona.')
    if review.get('technical_readiness') != 'ready':
        blockers.append('Gotowość techniczna nie ma ręcznego statusu ready.')
    if review.get('technical_blockers'):
        blockers.append('Przegląd techniczny zawiera nierozwiązane blokady.')
    return blockers


def refresh_preflight():
    hardware=hf_candidate_queue.hardware_snapshot()
    available=_memory_available()
    approved=hf_candidate_downloads.APPROVED_ARTIFACTS
    discovered=[]
    for item in _verified_artifacts():
        key=item['artifact_key']
        try: catalog_id,revision,filename=key.split('|',2)
        except ValueError: continue
        path=Path(item['path'])
        repo_item=next((x for x in hf_candidate_downloads.audit.load_catalog() if x['catalog_id']==catalog_id),None)
        repo_id=repo_item.get('repo_id','') if repo_item else ''
        expected_key=(catalog_id,revision,filename)
        estimated=int(item['size_bytes']*MEMORY_MULTIPLIER)+MEMORY_RESERVE_BYTES
        blockers=[]
        if expected_key not in approved and expected_key not in RUNTIME_CANDIDATES:
            blockers.append('Artefakt nie znajduje się na jawnej allowliście runtime ani liście kandydatów do przeglądu.')
        if expected_key in RUNTIME_CANDIDATES:
            with league.connect() as review_connection:
                blockers.extend(_review_blockers(review_connection, catalog_id, revision))
        try:
            if not path.resolve().is_relative_to(hf_candidate_downloads.STAGING_DIR.resolve()): blockers.append('Ścieżka artefaktu znajduje się poza katalogiem kwarantanny.')
        except (OSError, ValueError): blockers.append('Nie można potwierdzić bezpiecznej ścieżki kwarantanny.')
        if not path.is_file(): blockers.append('Plik kwarantanny nie istnieje.')
        else:
            st=path.stat()
            if st.st_size!=item['size_bytes'] or st.st_mtime_ns!=item['mtime_ns']:
                blockers.append('Rozmiar lub mtime różni się od rekordu zweryfikowanego artefaktu.')
            if st.st_size < 1: blockers.append('Plik jest pusty.')
        if available is None: blockers.append('Nie można wiarygodnie odczytać RAM; fail-closed.')
        elif available < estimated: blockers.append(f'Za mało RAM: potrzeba ok. {estimated/(1024**3):.2f} GiB, dostępne {available/(1024**3):.2f} GiB.')
        try:
            probe=hf_candidate_downloads.STAGING_DIR
            while not probe.exists() and probe!=probe.parent: probe=probe.parent
            free=shutil.disk_usage(probe).free
            if free < DISK_RESERVE_BYTES: blockers.append('Wolne miejsce na woluminie kwarantanny spadło poniżej rezerwy 512 MiB.')
        except OSError: blockers.append('Nie można odczytać wolnego miejsca woluminu kwarantanny.')
        status='skipped_blocked' if blockers else 'ready_pending_approval'
        discovered.append({'artifact_key':key,'catalog_id':catalog_id,'repo_id':repo_id,'revision':revision,
            'filename':filename,'path':str(path),'size_bytes':item['size_bytes'],'sha256':item['sha256'],
            'status':status,'reason':' '.join(blockers) if blockers else 'Preflight zasobów i metadanych OK. SHA-256 musi być ponownie sprawdzony bezpośrednio przed ewentualnym uruchomieniem. Test runtime wymaga osobnego jawnego zatwierdzenia.',
            'estimated_ram_bytes':estimated,'available_ram_bytes':available,'checked_at':_now()})
    discovered_keys={item['artifact_key'] for item in discovered}
    for catalog_id, revision, filename in sorted(RUNTIME_CANDIDATES):
        key='|'.join((catalog_id, revision, filename))
        if key in discovered_keys:
            continue
        repo_item=next((x for x in hf_candidate_downloads.audit.load_catalog() if x['catalog_id']==catalog_id),None)
        blockers=['Brak rekordu artefaktu zweryfikowanego w kwarantannie; plik nie jest dostępny dla preflight.']
        with league.connect() as review_connection:
            blockers.extend(_review_blockers(review_connection, catalog_id, revision))
        discovered.append({'artifact_key':key,'catalog_id':catalog_id,'repo_id':repo_item.get('repo_id','') if repo_item else '',
            'revision':revision,'filename':filename,'path':'','size_bytes':0,'sha256':'','status':'skipped_blocked',
            'reason':' '.join(blockers),'estimated_ram_bytes':None,'available_ram_bytes':available,'checked_at':_now()})

    with league.connect() as c:
        _init(c)
        current_keys={item['artifact_key'] for item in discovered}
        for stale in c.execute('SELECT artifact_key,history_json FROM hf_runtime_preflight').fetchall():
            if stale['artifact_key'] in current_keys: continue
            history=json.loads(stale['history_json'] or '[]')
            history.append({'at':_now(),'status':'skipped_blocked','reason':'Artefakt nie figuruje już w rejestrze zweryfikowanych plików kwarantanny.'})
            c.execute("UPDATE hf_runtime_preflight SET status='skipped_blocked',reason=?,checked_at=?,history_json=? WHERE artifact_key=?",
                ('Artefakt nie figuruje już w rejestrze zweryfikowanych plików kwarantanny.',_now(),json.dumps(history,ensure_ascii=False),stale['artifact_key']))
        for item in discovered:
            old=c.execute('SELECT history_json,status,reason FROM hf_runtime_preflight WHERE artifact_key=?',(item['artifact_key'],)).fetchone()
            history=json.loads(old['history_json']) if old else []
            if not old or old['status']!=item['status'] or old['reason']!=item['reason']:
                history.append({'at':item['checked_at'],'status':item['status'],'reason':item['reason']})
            c.execute('''INSERT INTO hf_runtime_preflight(artifact_key,catalog_id,repo_id,revision,filename,path,size_bytes,sha256,status,reason,estimated_ram_bytes,available_ram_bytes,checked_at,history_json)
                VALUES(:artifact_key,:catalog_id,:repo_id,:revision,:filename,:path,:size_bytes,:sha256,:status,:reason,:estimated_ram_bytes,:available_ram_bytes,:checked_at,:history)
                ON CONFLICT(artifact_key) DO UPDATE SET path=excluded.path,size_bytes=excluded.size_bytes,sha256=excluded.sha256,status=excluded.status,reason=excluded.reason,estimated_ram_bytes=excluded.estimated_ram_bytes,available_ram_bytes=excluded.available_ram_bytes,checked_at=excluded.checked_at,history_json=excluded.history_json''',
                {**item,'history':json.dumps(history,ensure_ascii=False)})
        rows=c.execute('SELECT * FROM hf_runtime_preflight ORDER BY checked_at DESC').fetchall()
    return _format(rows,hardware)


def _format(rows,hardware):
    items=[]
    for row in rows:
        item=dict(row);item['history']=json.loads(item.pop('history_json') or '[]');items.append(item)
    return {'items':items,'summary':{'verified_quarantined':len(items),'ready_pending_approval':sum(x['status']=='ready_pending_approval' for x in items),'skipped_blocked':sum(x['status']=='skipped_blocked' for x in items),'runtime_active':0,'runtime_enabled':False},'hardware':hardware,
        'policy':{'max_parallel_models':1,'auto_start':False,'runtime_enabled':False,'quarantine_files_moved':False,'requires_explicit_approval':True}}


def snapshot():
    with league.connect() as c:
        _init(c);rows=c.execute('SELECT * FROM hf_runtime_preflight ORDER BY checked_at DESC').fetchall()
    return _format(rows,hf_candidate_queue.hardware_snapshot())


def start_background_planner():
    import threading
    def loop():
        while True:
            try: refresh_preflight()
            except Exception as exc: print('HF runtime preflight refresh failed:',exc,flush=True)
            time.sleep(REFRESH_SECONDS)
    try: refresh_preflight()
    except Exception as exc: print('HF runtime preflight initial refresh failed:',exc,flush=True)
    threading.Thread(target=loop,daemon=True,name='hf-runtime-preflight').start()
