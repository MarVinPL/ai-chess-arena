"""Opt-in, revision-pinned Hugging Face artifact downloads into quarantine staging.

Downloaded artifacts are never registered, activated, or placed in managed-models.
"""
from __future__ import annotations
import hashlib, json, os, re, shutil, threading, time, uuid
from pathlib import Path
from urllib.parse import quote, urlparse
from urllib.request import Request, build_opener, HTTPRedirectHandler

import hf_candidate_audit as audit

STAGING_DIR = Path('/var/lib/ai-chess-arena/hf-candidate-downloads')
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
        files.append({'name':name,'size_bytes':size,'sha256':match.group(1).lower(),'revision':revision})
    return {'catalog_id':catalog_id,'repo_id':repo,'revision':revision,'files':files,'free_bytes':_free_bytes()}

class _Redirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        p=urlparse(newurl); host=(p.hostname or '').lower()
        allowed=host=='huggingface.co' or host.endswith('.huggingface.co') or host.endswith('.xethub.hf.co') or host.endswith('.hf.co')
        if p.scheme!='https' or not allowed or p.username or p.password: raise DownloadError('Niebezpieczne przekierowanie pobierania zablokowane.')
        return super().redirect_request(req,fp,code,msg,headers,newurl)

def _download(job, repo, revision, file, cancel_event):
    name=file['name']; size=file['size_bytes']; expected=file['sha256']
    target_dir=STAGING_DIR / job['id']; target_dir.mkdir(mode=0o700,parents=True,exist_ok=True)
    repo_tag=hashlib.sha256(repo.encode()).hexdigest()[:10]
    target=target_dir / (repo_tag + '__' + name.replace('/','__') + '.quarantine')
    part=target.with_suffix(target.suffix+'.part')
    if target.exists() or part.exists(): raise DownloadError('Plik staging już istnieje; brak nadpisywania.')
    free=shutil.disk_usage(STAGING_DIR).free
    if free < size + MIN_FREE_BYTES: raise DownloadError('Za mało wolnego miejsca z marginesem 2 GiB.')
    url=f'https://huggingface.co/{quote(repo,safe="/")}/resolve/{revision}/{quote(name,safe="/")}?download=true'
    req=Request(url,headers={'User-Agent':'AI-Chess-Arena-HF-Quarantine/1.0'})
    digest=hashlib.sha256(); received=0
    try:
        with build_opener(_Redirects()).open(req,timeout=60) as response, part.open('xb') as out:
            final=urlparse(response.geturl()); host=(final.hostname or '').lower()
            if final.scheme!='https' or not (host=='huggingface.co' or host.endswith('.huggingface.co') or host.endswith('.xethub.hf.co') or host.endswith('.hf.co')): raise DownloadError('Końcowy host pobierania nie jest dozwolony.')
            while True:
                if cancel_event.is_set(): raise DownloadError('Pobieranie anulowane przez operatora.')
                block=response.read(CHUNK)
                if not block: break
                received+=len(block)
                if received>size: raise DownloadError('Pobrany plik przekracza rozmiar z metadanych.')
                digest.update(block); out.write(block)
                with _lock:
                    job['current_file_bytes']=received; job['updated_at']=_now()
            out.flush(); os.fsync(out.fileno())
        actual=digest.hexdigest()
        if received!=size or actual!=expected: raise DownloadError('Rozmiar lub SHA-256 nie zgadza się z metadanymi LFS.')
        os.chmod(part,0o600); os.replace(part,target)
        return {'name':name,'size_bytes':received,'sha256':actual,'status':'verified_quarantined','path':str(target)}
    except Exception:
        try: part.unlink()
        except FileNotFoundError: pass
        raise

def _worker(job_id, tasks):
    global _active
    with _lock: job=_jobs[job_id]
    try:
        for index,task in enumerate(tasks):
            file=task['file']; repo=task['repo_id']; revision=task['revision']
            with _lock:
                job.update(status='downloading',current_file=repo+'/'+file['name'],current_file_bytes=0,completed_files=index,updated_at=_now())
            result=_download(job,repo,revision,file,_cancel_events[job_id])
            result.update({'catalog_id':task['catalog_id'],'repo_id':repo,'revision':revision})
            with _lock: job['results'].append(result); job['completed_files']=index+1
        with _lock: job.update(status='completed',message='Wszystkie pliki pobrano i zweryfikowano w kwarantannie; nie są dostępne dla silnika.',updated_at=_now())
    except Exception as exc:
        with _lock: job.update(status='cancelled' if _cancel_events[job_id].is_set() else 'failed',message=str(exc),updated_at=_now())
    finally:
        with _lock:
            _active=False
            _cancel_events.pop(job_id,None)

def start(candidates: list):
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
        job={'id':job_id,'catalogs':list(catalog_map.values()),'status':'queued','total_bytes':total,'total_files':len(tasks),'completed_files':0,'current_file':None,'current_file_bytes':0,'results':[],'created_at':_now(),'updated_at':_now(),'message':'Oczekiwanie na pobieranie.'}
        _jobs[job_id]=job
        _cancel_events[job_id]=threading.Event()
        thread=threading.Thread(target=_worker,args=(job_id,tasks),daemon=True)
        thread.start()
        return dict(job)

def get(job_id):
    with _lock:
        job=_jobs.get(job_id)
        return dict(job) if job else None


def cancel(job_id):
    with _lock:
        job=_jobs.get(job_id); event=_cancel_events.get(job_id)
        if not job: raise DownloadError('Nie znaleziono zadania.')
        if job.get('status') not in {'queued','downloading'} or event is None: raise DownloadError('Tego zadania nie można już anulować.')
        event.set(); job['message']='Przyjęto żądanie anulowania.'; job['updated_at']=_now()
        return dict(job)
