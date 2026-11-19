"""Opt-in, revision-pinned Hugging Face artifact downloads into quarantine staging.

Downloaded artifacts are never registered, activated, or placed in managed-models.
"""
from __future__ import annotations
import hashlib, json, os, re, shutil, sqlite3, threading, time, uuid, http.client
from pathlib import Path
from urllib.parse import quote, urlparse
from urllib.error import URLError, HTTPError
from urllib.request import Request, build_opener, HTTPRedirectHandler

import hf_candidate_audit as audit

STAGING_DIR = Path('/var/lib/ai-chess-arena/hf-candidate-downloads')
DB_PATH = Path(__file__).resolve().parent / 'arena.sqlite3'
APPROVED_ARTIFACTS = {
 ('hf:abenzerps/Apodex-1.1-mini-GGUF','e44e0cbd3151d08a9f5a4cbf19cd44560685de04','Apodex-1.1-mini-IQ1_M.gguf'),
}
MAX_BATCH_FILES = 3
MAX_BATCH_BYTES = 16 * 1024**3
MIN_FREE_BYTES = 2 * 1024**3
CHUNK = 1024 * 1024
_jobs: dict[str, dict] = {}
_lock = threading.RLock()
_active = False
_cancel_events: dict[str, threading.Event] = {}

class DownloadError(ValueError): pass

def _now(): return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())

def _db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=15)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA busy_timeout=15000')
    return c

def init_db(path=None):
    global DB_PATH, _active
    if path is not None: DB_PATH = Path(path)
    with _db() as c:
        c.execute("CREATE TABLE IF NOT EXISTS hf_download_jobs(id TEXT PRIMARY KEY,status TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,payload_json TEXT NOT NULL)")
        c.execute("CREATE TABLE IF NOT EXISTS hf_download_events(id INTEGER PRIMARY KEY AUTOINCREMENT,job_id TEXT NOT NULL,occurred_at TEXT NOT NULL,event TEXT NOT NULL,details_json TEXT NOT NULL DEFAULT '{}')")
        c.execute("CREATE TABLE IF NOT EXISTS hf_download_queue(artifact_key TEXT PRIMARY KEY,catalog_id TEXT NOT NULL,repo_id TEXT NOT NULL,revision TEXT NOT NULL,filename TEXT NOT NULL,sha256 TEXT,size_bytes INTEGER,status TEXT NOT NULL,reason TEXT NOT NULL,priority INTEGER NOT NULL,updated_at TEXT NOT NULL,payload_json TEXT NOT NULL)")
        c.execute("CREATE TABLE IF NOT EXISTS hf_download_verified_artifacts(artifact_key TEXT PRIMARY KEY,path TEXT NOT NULL,size_bytes INTEGER NOT NULL,sha256 TEXT NOT NULL,verified_at TEXT NOT NULL,mtime_ns INTEGER NOT NULL)")
        restored = {}
        for row in c.execute('SELECT payload_json FROM hf_download_jobs ORDER BY created_at DESC LIMIT 100').fetchall():
            try:
                job = json.loads(row['payload_json'])
                if job.get('status') in {'queued','downloading'}:
                    job.update(status='interrupted', message='Proces został uruchomiony ponownie. Zadanie zachowano; można bezpiecznie wznowić.', updated_at=_now())
                    c.execute('UPDATE hf_download_jobs SET status=?,updated_at=?,payload_json=? WHERE id=?',(job['status'],job['updated_at'],json.dumps(job,ensure_ascii=False),job['id']))
                    c.execute('INSERT INTO hf_download_events(job_id,occurred_at,event,details_json) VALUES(?,?,?,?)',(job['id'],job['updated_at'],'interrupted',json.dumps({'reason':'process_restart'})))
                restored[job['id']] = job
            except (ValueError,TypeError,KeyError): pass
    with _lock:
        _jobs.clear(); _jobs.update(restored); _cancel_events.clear(); _active=False
    return {'restored_jobs':len(restored)}

def _persist(job, event=None, details=None):
    with _db() as c:
        c.execute('INSERT INTO hf_download_jobs(id,status,created_at,updated_at,payload_json) VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET status=excluded.status,updated_at=excluded.updated_at,payload_json=excluded.payload_json',
                  (job['id'],job['status'],job['created_at'],job['updated_at'],json.dumps(job,ensure_ascii=False)))
        if event: c.execute('INSERT INTO hf_download_events(job_id,occurred_at,event,details_json) VALUES(?,?,?,?)',(job['id'],job['updated_at'],event,json.dumps(details or {},ensure_ascii=False)))

def _artifact_key(catalog_id, revision, filename): return '|'.join((catalog_id,revision,filename))

def _find_existing_verified(catalog_id, repo, revision, file):
    key=_artifact_key(catalog_id,revision,file['name'])
    with _db() as c: row=c.execute('SELECT * FROM hf_download_verified_artifacts WHERE artifact_key=?',(key,)).fetchone()
    if row and Path(row['path']).is_file():
        st=os.stat(row['path'])
        if st.st_size==file['size_bytes'] and st.st_mtime_ns==row['mtime_ns'] and row['sha256']==file['sha256']: return {'path':row['path'],'sha256':row['sha256'],'size_bytes':row['size_bytes']}
    repo_tag=hashlib.sha256(repo.encode()).hexdigest()[:10]; wanted=repo_tag+'__'+file['name'].replace('/','__')+'.quarantine'
    if STAGING_DIR.exists():
        for path in STAGING_DIR.glob('*/'+wanted):
            try:
                st=path.stat()
                if st.st_size!=file['size_bytes']: continue
                digest=hashlib.sha256()
                with path.open('rb') as stream:
                    for block in iter(lambda:stream.read(CHUNK),b''): digest.update(block)
                actual=digest.hexdigest()
                if actual==file['sha256']:
                    with _db() as c: c.execute('INSERT OR REPLACE INTO hf_download_verified_artifacts VALUES(?,?,?,?,?,?)',(key,str(path),st.st_size,actual,_now(),st.st_mtime_ns))
                    return {'path':str(path),'sha256':actual,'size_bytes':st.st_size}
            except OSError: pass
    return None

def _set_queue_status(task,status,reason):
    key=_artifact_key(task['catalog_id'],task['revision'],task['file']['name'])
    with _db() as c:
        row=c.execute('SELECT payload_json FROM hf_download_queue WHERE artifact_key=?',(key,)).fetchone()
        if row:
            item=json.loads(row['payload_json']);item.update(status=status,reason=reason,updated_at=_now())
            c.execute('UPDATE hf_download_queue SET status=?,reason=?,updated_at=?,payload_json=? WHERE artifact_key=?',(status,reason,item['updated_at'],json.dumps(item,ensure_ascii=False),key))

def _record_verified(task,result):
    key=_artifact_key(task['catalog_id'],task['revision'],task['file']['name']);st=os.stat(result['path'])
    with _db() as c:
        c.execute('INSERT OR REPLACE INTO hf_download_verified_artifacts VALUES(?,?,?,?,?,?)',(key,result['path'],result['size_bytes'],result['sha256'],_now(),st.st_mtime_ns))

def _free_bytes():
    probe=STAGING_DIR
    while not probe.exists() and probe!=probe.parent:
        probe=probe.parent
    return shutil.disk_usage(probe).free

def list_files(catalog_id: str):
    item = audit._candidate(catalog_id)
    repo = item['repo_id']; revision = item.get('revision') or item.get('audited_revision')
    raw = audit._request(f'{audit.API_BASE}{quote(repo, safe="/")}/revision/{revision}?blobs=true', max_bytes=audit.MAX_METADATA_BYTES)
    info = json.loads(raw.decode('utf-8'))
    if info.get('sha') != revision: raise DownloadError('Rewizja API nie zgadza się z przypiętym SHA.')
    files=[]
    for entry in info.get('siblings', []):
        if not isinstance(entry, dict): continue
        name=entry.get('rfilename'); lfs=entry.get('lfs') or {}
        if not isinstance(name,str) or not name.lower().endswith('.gguf'): continue
        if any(x in ('','.','..') for x in name.split('/')) or not all(re.fullmatch(r'[A-Za-z0-9_.+-]+',x) for x in name.split('/')): continue
        size=entry.get('size') or lfs.get('size'); oid=lfs.get('sha256') or lfs.get('oid') or entry.get('oid')
        if not isinstance(size,int) or size<=0 or not isinstance(oid,str): continue
        match=re.fullmatch(r'(?:sha256:)?([0-9a-fA-F]{64})',oid)
        if not match: continue
        files.append({'name':name,'size_bytes':size,'sha256':match.group(1).lower(),'revision':revision,'allowlisted':(catalog_id,revision,name) in APPROVED_ARTIFACTS})
    for file in files: file['allowlisted']=(catalog_id,revision,file['name']) in APPROVED_ARTIFACTS
    return {'catalog_id':catalog_id,'repo_id':repo,'revision':revision,'files':files,'free_bytes':_free_bytes()}

class _Redirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        p=urlparse(newurl); host=(p.hostname or '').lower()
        allowed=host=='huggingface.co' or host.endswith('.huggingface.co') or host.endswith('.xethub.hf.co') or host.endswith('.hf.co')
        if p.scheme!='https' or not allowed or p.username or p.password: raise DownloadError('Niebezpieczne przekierowanie pobierania zablokowane.')
        return super().redirect_request(req,fp,code,msg,headers,newurl)

def _download(job, repo, revision, file, cancel_event):
    name=file['name']; size=file['size_bytes']; expected=file['sha256']
    target_dir=STAGING_DIR/job['id']; target_dir.mkdir(mode=0o700,parents=True,exist_ok=True)
    repo_tag=hashlib.sha256(repo.encode()).hexdigest()[:10]
    target=target_dir/(repo_tag+'__'+name.replace('/','__')+'.quarantine'); part=target.with_suffix(target.suffix+'.part')
    if target.exists(): raise DownloadError('Zweryfikowany plik docelowy już istnieje; brak nadpisywania.')
    offset=part.stat().st_size if part.exists() else 0
    if offset>size: raise DownloadError('Plik częściowy przekracza rozmiar docelowy; wymaga ręcznej kontroli.')
    if shutil.disk_usage(STAGING_DIR).free < max(0,size-offset)+MIN_FREE_BYTES: raise DownloadError('Za mało wolnego miejsca z marginesem 2 GiB.')
    url=f'https://huggingface.co/{quote(repo,safe="/")}/resolve/{revision}/{quote(name,safe="/")}?download=true'
    headers={'User-Agent':'AI-Chess-Arena-HF-Quarantine/1.0'}
    if offset: headers['Range']=f'bytes={offset}-'
    digest=hashlib.sha256();received=offset
    if offset:
        with part.open('rb') as prefix:
            for block in iter(lambda:prefix.read(CHUNK),b''): digest.update(block)
    last_persist=0;last_time=time.monotonic()
    with build_opener(_Redirects()).open(Request(url,headers=headers),timeout=60) as response:
        final=urlparse(response.geturl());host=(final.hostname or '').lower()
        if final.scheme!='https' or not (host=='huggingface.co' or host.endswith('.huggingface.co') or host.endswith('.xethub.hf.co') or host.endswith('.hf.co')): raise DownloadError('Końcowy host pobierania nie jest dozwolony.')
        status=getattr(response,'status',None)
        if status is None: status=response.getcode()
        if offset and status==206:
            if not response.headers.get('Content-Range','').startswith(f'bytes {offset}-'): raise DownloadError('Serwer zwrócił niezgodny zakres wznowienia.')
            mode='ab'
        else:
            if offset and status!=200: raise DownloadError('Serwer nie obsłużył bezpiecznego wznowienia.')
            if offset: digest=hashlib.sha256();received=0
            mode='wb' if offset else 'xb'
        with part.open(mode) as out:
            while True:
                if cancel_event.is_set(): raise DownloadError('Pobieranie anulowane przez operatora.')
                block=response.read(CHUNK)
                if not block: break
                received+=len(block)
                if received>size: raise DownloadError('Pobrany plik przekracza rozmiar z metadanych.')
                digest.update(block);out.write(block)
                with _lock:
                    job['current_file_bytes']=received;job['updated_at']=_now();now=time.monotonic()
                    if received-last_persist>=4*CHUNK or now-last_time>=2:
                        _persist(job);last_persist=received;last_time=now
            out.flush();os.fsync(out.fileno())
    actual=digest.hexdigest()
    if received!=size or actual!=expected: raise DownloadError('Rozmiar lub SHA-256 nie zgadza się z metadanymi LFS.')
    os.chmod(part,0o600);os.replace(part,target)
    return {'name':name,'size_bytes':received,'sha256':actual,'status':'verified_quarantined','path':str(target)}

def _worker(job_id,tasks):
    global _active
    with _lock: job=_jobs[job_id]
    try:
        completed={(x.get('catalog_id'),x.get('revision'),x.get('name')) for x in job.get('results',[])}
        for task in tasks:
            file=task['file'];repo=task['repo_id'];revision=task['revision']
            if (task['catalog_id'],revision,file['name']) in completed: continue
            # Recover if a process stopped after the verified rename but before job JSON was updated.
            existing=_find_existing_verified(task['catalog_id'],repo,revision,file)
            if existing:
                result={'name':file['name'],'size_bytes':file['size_bytes'],'sha256':file['sha256'],
                        'status':'verified_quarantined','path':existing['path'],
                        'catalog_id':task['catalog_id'],'repo_id':repo,'revision':revision}
                job['results'].append(result);completed.add((task['catalog_id'],revision,file['name']))
                job.update(completed_files=len(completed),updated_at=_now())
                _persist(job,'reconciled_verified_file',{'file':file['name'],'path':existing['path']})
                _set_queue_status(task,'verified_quarantined','Odzyskano po restarcie: istniejący plik ma zgodny SHA-256; pozostaje w kwarantannie.')
                continue
            with _lock:
                job.update(status='downloading',current_file=repo+'/'+file['name'],current_file_bytes=0,current_file_total_bytes=file['size_bytes'],completed_files=len(completed),updated_at=_now())
                _persist(job,'file_started',{'file':file['name'],'repo_id':repo})
            _set_queue_status(task,'downloading','Pobieranie do kwarantanny; trwa weryfikacja SHA-256.')
            result=_download(job,repo,revision,file,_cancel_events[job_id]);result.update({'catalog_id':task['catalog_id'],'repo_id':repo,'revision':revision});_record_verified(task,result)
            with _lock:
                job['results'].append(result);completed.add((task['catalog_id'],revision,file['name']));job.update(completed_files=len(completed),updated_at=_now())
                _persist(job,'file_verified',{'file':file['name'],'sha256':result['sha256'],'status':'verified_quarantined'})
            _set_queue_status(task,'verified_quarantined','SHA-256 zgodny; plik pozostaje w kwarantannie.')
        with _lock: job.update(status='completed',message='Wszystkie pliki pobrano i zweryfikowano w kwarantannie; nie są dostępne dla silnika.',updated_at=_now());_persist(job,'completed')
    except Exception as exc:
        retryable = isinstance(exc, (TimeoutError, ConnectionError, http.client.IncompleteRead)) or (isinstance(exc, URLError) and not isinstance(exc, HTTPError))
        # Keep a partial transfer resumable only for transient transport failures.
        # Integrity, policy, disk-space and cancellation failures remain terminal.
        has_partial = any((STAGING_DIR / job_id).glob('*.part')) if (STAGING_DIR / job_id).exists() else False
        with _lock:
            if _cancel_events.get(job_id,threading.Event()).is_set():
                status = 'cancelled'
            elif retryable and has_partial:
                status = 'interrupted'
            else:
                status = 'failed'
            job.update(status=status,message=str(exc),updated_at=_now())
            _persist(job,status,{'message':str(exc),'retryable_transport_error':retryable,'partial_file_preserved':has_partial})
    finally:
        with _lock: _active=False;_cancel_events.pop(job_id,None)

def start(candidates: list, source='manual'):
    global _active
    if not isinstance(candidates,list) or not candidates or len(candidates)>3:
        raise DownloadError('Wybierz od 1 do 3 kandydatów.')
    tasks=[]; seen_catalogs=set(); seen_files=set(); total=0; free_bytes=None
    for group in candidates:
        if not isinstance(group,dict): raise DownloadError('Nieprawidłowa grupa kandydata.')
        catalog_id=group.get('catalog_id'); selections=group.get('files')
        if not isinstance(catalog_id,str) or catalog_id in seen_catalogs: raise DownloadError('Powtórzony lub nieprawidłowy kandydat.')
        seen_catalogs.add(catalog_id)
        if not isinstance(selections,list) or not selections: raise DownloadError('Każdy kandydat musi mieć co najmniej jeden wybrany plik.')
        inventory=list_files(catalog_id); lookup={x['name']:x for x in inventory['files']}
        free_bytes=inventory['free_bytes'] if free_bytes is None else min(free_bytes,inventory['free_bytes'])
        for selection in selections:
            if not isinstance(selection,dict): raise DownloadError('Nieprawidłowy wybór pliku.')
            file=lookup.get(selection.get('name'))
            if not file or selection.get('sha256')!=file['sha256'] or selection.get('revision')!=inventory['revision']:
                raise DownloadError('Wybór nie zgadza się z aktualnym manifestem przypiętej rewizji.')
            if (catalog_id,inventory['revision'],file['name']) not in APPROVED_ARTIFACTS: raise DownloadError('Plik nie znajduje się na jawnej allowliście; planner nie może rozszerzać zgody samodzielnie.')
            if _find_existing_verified(catalog_id,inventory['repo_id'],inventory['revision'],file): raise DownloadError('Ten dokładny plik jest już zweryfikowany w kwarantannie.')
            key=(catalog_id,file['name'])
            if key in seen_files: raise DownloadError('Powtórzony plik na liście.')
            seen_files.add(key); total+=file['size_bytes']
            tasks.append({'catalog_id':catalog_id,'repo_id':inventory['repo_id'],'revision':inventory['revision'],'file':file})
            if len(tasks)>MAX_BATCH_FILES: raise DownloadError('Partia może zawierać maksymalnie 3 pliki.')
    if total>MAX_BATCH_BYTES: raise DownloadError('Partia przekracza limit 16 GiB.')
    if free_bytes is None or free_bytes<total+MIN_FREE_BYTES: raise DownloadError('Za mało wolnego miejsca na VPS z marginesem 2 GiB.')
    with _lock:
        if _active: raise DownloadError('Inna partia pobierania już trwa.')
        _active=True
        job_id=uuid.uuid4().hex
        catalog_map={}
        for task in tasks:
            catalog_map.setdefault(task['catalog_id'], {'catalog_id':task['catalog_id'],'repo_id':task['repo_id'],'revision':task['revision']})
        job={'id':job_id,'catalogs':list(catalog_map.values()),'tasks':tasks,'source':source,'status':'queued','total_bytes':total,'total_files':len(tasks),'completed_files':0,'current_file':None,'current_file_bytes':0,'current_file_total_bytes':0,'results':[],'created_at':_now(),'updated_at':_now(),'message':'Oczekiwanie na pobieranie.'}
        _jobs[job_id]=job
        _cancel_events[job_id]=threading.Event()
        _persist(job,'queued',{'total_files':len(tasks),'total_bytes':total})
        thread=threading.Thread(target=_worker,args=(job_id,tasks),daemon=True)
        thread.start()
        return dict(job)

def _snapshot(job):
    snapshot=dict(job)
    completed_bytes=sum(int(item.get('size_bytes') or 0) for item in snapshot.get('results', []))
    current_bytes=int(snapshot.get('current_file_bytes') or 0) if snapshot.get('status') in {'queued','downloading','interrupted'} else 0
    snapshot['downloaded_bytes']=completed_bytes+current_bytes
    total_bytes=int(snapshot.get('total_bytes') or 0)
    snapshot['progress_percent']=round(min(100.0, snapshot['downloaded_bytes'] * 100.0 / total_bytes), 1) if total_bytes else 0.0
    snapshot['active']=snapshot.get('status') in {'queued','downloading'}
    return snapshot


def get(job_id):
    with _lock: job=_jobs.get(job_id)
    if job is None:
        with _db() as c: row=c.execute('SELECT payload_json FROM hf_download_jobs WHERE id=?',(job_id,)).fetchone()
        if row: job=json.loads(row['payload_json']);_jobs[job_id]=job
    return _snapshot(job) if job else None

def current():
    with _lock:
        if _jobs: return _snapshot(max(enumerate(_jobs.values()),key=lambda pair:(pair[1].get('created_at') or '',pair[0]))[1])
    with _db() as c: row=c.execute('SELECT payload_json FROM hf_download_jobs ORDER BY created_at DESC LIMIT 1').fetchone()
    if not row: return None
    job=json.loads(row['payload_json']);_jobs[job['id']]=job;return _snapshot(job)

def history(limit=30):
    with _db() as c: rows=c.execute('SELECT payload_json FROM hf_download_jobs ORDER BY created_at DESC LIMIT ?',(max(1,min(int(limit),100)),)).fetchall()
    return [_snapshot(json.loads(row['payload_json'])) for row in rows]

def events(job_id,limit=50):
    with _db() as c: rows=c.execute('SELECT occurred_at,event,details_json FROM hf_download_events WHERE job_id=? ORDER BY id DESC LIMIT ?',(job_id,max(1,min(int(limit),200)))).fetchall()
    return [{'occurred_at':r['occurred_at'],'event':r['event'],'details':json.loads(r['details_json'])} for r in rows]

def resume(job_id):
    global _active
    with _lock: job=_jobs.get(job_id)
    if job is None:
        with _db() as c: row=c.execute('SELECT payload_json FROM hf_download_jobs WHERE id=?',(job_id,)).fetchone()
        if row: job=json.loads(row['payload_json']);_jobs[job_id]=job
    if not job or job.get('status') != 'interrupted': raise DownloadError('Nie znaleziono zadania z zachowanym transferem do wznowienia.')
    with _lock:
        if _active: raise DownloadError('Inna partia pobierania już trwa.')
        for task in job.get('tasks',[]):
            if (task['catalog_id'],task['revision'],task['file']['name']) not in APPROVED_ARTIFACTS: raise DownloadError('Zadanie poza jawną allowlistą; wznowienie zablokowane.')
            inv=list_files(task['catalog_id']);match=next((x for x in inv['files'] if x['name']==task['file']['name']),None)
            if not match or match['sha256']!=task['file']['sha256'] or match['revision']!=task['revision']: raise DownloadError('Metadane pliku zmieniły się; wznowienie zablokowane.')
        job.update(status='queued',message='Wznawianie z zachowanego pliku częściowego.',updated_at=_now());_persist(job,'resume_requested')
        _active=True;_cancel_events[job_id]=threading.Event();threading.Thread(target=_worker,args=(job_id,job['tasks']),daemon=True).start();return _snapshot(job)


def cancel(job_id):
    with _lock:
        job=_jobs.get(job_id); event=_cancel_events.get(job_id)
        if not job: raise DownloadError('Nie znaleziono zadania.')
        if job.get('status') not in {'queued','downloading'} or event is None: raise DownloadError('Tego zadania nie można już anulować.')
        event.set(); job['message']='Przyjęto żądanie anulowania.'; job['updated_at']=_now();_persist(job,'cancel_requested')
        return dict(job)


def settings():
    return {'auto_download_enabled':False,'manual_confirmation_required':True,'max_batch_files':MAX_BATCH_FILES,'max_batch_bytes':MAX_BATCH_BYTES,'min_free_bytes':MIN_FREE_BYTES,'allowlist':[{'catalog_id':x[0],'revision':x[1],'filename':x[2]} for x in sorted(APPROVED_ARTIFACTS)]}

def refresh_plan():
    catalog=audit.load_catalog(); by_id={x['catalog_id']:x for x in catalog if isinstance(x,dict) and x.get('catalog_id')}; items=[]
    for catalog_id,revision,filename in sorted(APPROVED_ARTIFACTS):
        candidate=by_id.get(catalog_id); key=_artifact_key(catalog_id,revision,filename)
        item={'artifact_key':key,'catalog_id':catalog_id,'repo_id':candidate.get('repo_id') if candidate else '', 'revision':revision,'filename':filename,'priority':100,'status':'blocked','size_bytes':None,'sha256':None}; reason=[]
        if not candidate: reason.append('Kandydat nie występuje w aktualnym katalogu.')
        elif candidate.get('source_status')!='active': reason.append('Źródło kandydata nie jest aktywne.')
        elif (candidate.get('revision') or candidate.get('audited_revision'))!=revision: reason.append('Rewizja zmieniła się; wcześniejsza zgoda nie obejmuje nowej rewizji.')
        else:
            try:
                inv=list_files(catalog_id); file=next((x for x in inv['files'] if x['name']==filename),None)
                if not file: reason.append('Nie znaleziono dokładnego pliku w przypiętej rewizji.')
                else:
                    item.update(repo_id=inv['repo_id'],size_bytes=file['size_bytes'],sha256=file['sha256'],free_bytes=inv['free_bytes'])
                    if file['revision']!=revision: reason.append('Niezgodna rewizja metadanych.')
                    elif inv['free_bytes']<file['size_bytes']+MIN_FREE_BYTES: reason.append('Za mało wolnego miejsca z zapasem 2 GiB.')
                    else:
                        existing=_find_existing_verified(catalog_id,inv['repo_id'],revision,file)
                        if existing: item.update(status='verified_quarantined',existing_path=existing['path']);reason.append('Plik pobrany i SHA-256 zgodny; pozostaje w kwarantannie.')
                        else: item['status']='planned';reason.append('Dokładny artefakt znajduje się na jawnej allowliście.')
            except Exception as exc: reason.append('Nie udało się zweryfikować metadanych: '+str(exc))
        item['reason']=' '.join(reason);item['updated_at']=_now();items.append(item)
    now=_now()
    with _db() as c:
        for item in items:
            c.execute('INSERT INTO hf_download_queue VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(artifact_key) DO UPDATE SET repo_id=excluded.repo_id,sha256=excluded.sha256,size_bytes=excluded.size_bytes,status=excluded.status,reason=excluded.reason,priority=excluded.priority,updated_at=excluded.updated_at,payload_json=excluded.payload_json',(item['artifact_key'],item['catalog_id'],item['repo_id'],item['revision'],item['filename'],item['sha256'],item['size_bytes'],item['status'],item['reason'],item['priority'],now,json.dumps(item,ensure_ascii=False)))
        rows=c.execute('SELECT payload_json FROM hf_download_queue ORDER BY priority DESC,updated_at DESC').fetchall()
    stored=[json.loads(r['payload_json']) for r in rows]
    return {'items':stored,'summary':{'catalog_candidates':len(catalog),'approved_artifacts':len(APPROVED_ARTIFACTS),'planned':sum(x['status']=='planned' for x in stored),'verified_quarantined':sum(x['status']=='verified_quarantined' for x in stored),'blocked':sum(x['status']=='blocked' for x in stored)},'settings':settings()}

def plan_snapshot():
    with _db() as c: rows=c.execute('SELECT payload_json FROM hf_download_queue ORDER BY priority DESC,updated_at DESC').fetchall()
    items=[json.loads(r['payload_json']) for r in rows]
    return {'items':items,'summary':{'catalog_candidates':len(audit.load_catalog()),'approved_artifacts':len(APPROVED_ARTIFACTS),'planned':sum(x['status']=='planned' for x in items),'verified_quarantined':sum(x['status']=='verified_quarantined' for x in items),'blocked':sum(x['status']=='blocked' for x in items)},'settings':settings()}
