"""Metadata-only discovery and verification of Hugging Face GGUF text models.

The candidate catalog is informational. It never updates the pinned model manifest,
allowlist, database, tournament roster, or model files, and never runs inference.
"""
from __future__ import annotations
import json, os, re, tempfile, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timezone
from pathlib import Path
from model_exclusions import is_excluded_model_identity

ROOT=Path(__file__).resolve().parent
SOURCE_MANIFEST=ROOT/'manifests/huggingface-models.json'
CATALOG_PATH=ROOT/'manifests/huggingface-candidates.json'
REPORT_PATH=ROOT/'reports/huggingface-catalog-sync.json'
API_ROOT='https://huggingface.co/api/models'
SOURCE_MODEL_IDS=frozenset(range(1,9))
LIMIT_PER_QUERY=100
MAX_TRACKED_CANDIDATES=500
_REPO=re.compile(r'^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$')
_SHA=re.compile(r'^[0-9a-f]{40}$')
_BLOCKED_TERMS=frozenset({'adult','nsfw','not-safe-for-work','porn','pornography','pornmaster','hentai','erotic','erotica','sexual','sex','explicit','smut','lewd','rule34','adult-content','adultonly','nsfl','uncensored','xxx','fetish','nudity','nude','18+','18plus','suicide','self-harm','weapon','firearm','ammunition','explosive','explosives','bomb','drug','drugs','narcotic','opioid','cocaine','methamphetamine'})

class CatalogSyncError(RuntimeError):
    """A source or manifest error that aborts without replacing the last catalog."""

class HTTPStatusError(CatalogSyncError):
    def __init__(self,status,url):
        super().__init__(f'Hugging Face API HTTP {status}'); self.status=status; self.url=url

def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00','Z')

def fetch_json(url,timeout=20):
    req=urllib.request.Request(url,headers={'User-Agent':'AI-Chess-Arena-Candidate-Catalog/1.0','Accept':'application/json'})
    try:
        with urllib.request.urlopen(req,timeout=timeout) as response: return json.load(response),response.geturl()
    except urllib.error.HTTPError as exc: raise HTTPStatusError(exc.code,url) from exc
    except (urllib.error.URLError,TimeoutError,json.JSONDecodeError) as exc: raise CatalogSyncError(f'Błąd odczytu metadanych Hugging Face: {type(exc).__name__}') from exc

def _list_url(sort):
    if sort not in {'lastModified','downloads'}: raise ValueError('Unsupported sort')
    params=[('filter','gguf'),('sort',sort),('direction','-1'),('limit',str(LIMIT_PER_QUERY))]
    params += [('expand[]',x) for x in ('sha','lastModified','downloads','likes','tags','private','gated','disabled')]
    return API_ROOT+'?'+urllib.parse.urlencode(params)

def _load_source_models(path):
    try: payload=json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError,json.JSONDecodeError) as exc: raise CatalogSyncError('Nie można odczytać źródłowego manifestu modeli') from exc
    models=payload.get('models') if isinstance(payload,dict) else None
    if not isinstance(models,list): raise CatalogSyncError('Źródłowy manifest nie zawiera listy modeli')
    selected=sorted((x for x in models if isinstance(x,dict) and x.get('model_id') in SOURCE_MODEL_IDS),key=lambda x:x['model_id'])
    if [x.get('model_id') for x in selected]!=list(range(1,9)): raise CatalogSyncError('Źródłowy manifest musi zawierać dokładnie modele ID 1–8')
    seen=set()
    for item in selected:
        repo,revision=item.get('repo_id'),item.get('revision')
        if not isinstance(repo,str) or not _REPO.fullmatch(repo) or repo in seen: raise CatalogSyncError('Nieprawidłowy lub powtórzony repo_id w źródłowym manifeście')
        if not isinstance(revision,str) or not _SHA.fullmatch(revision): raise CatalogSyncError('Nieprawidłowa przypięta rewizja w źródłowym manifeście')
        seen.add(repo)
    return selected

def _repo_from_api_url(url):
    parsed=urllib.parse.urlsplit(url); prefix='/api/models/'
    if parsed.netloc.lower()=='huggingface.co' and parsed.path.startswith(prefix): return urllib.parse.unquote(parsed.path[len(prefix):]).strip('/')
    return None

def _is_safety_filtered(repo,tags):
    tagset={str(x).casefold() for x in tags if isinstance(x,str)}
    if is_excluded_model_identity(source=f'https://huggingface.co/{repo}',metadata={'repo_id':repo}): return True
    haystack=' '.join([repo,*tagset]).casefold()
    return any(term in tagset or re.search(r'(?<![a-z0-9])'+re.escape(term)+r'(?![a-z0-9])',haystack) for term in _BLOCKED_TERMS)

def _source_status(raw,repo,tags):
    if raw.get('gated') is True: return 'gated'
    if raw.get('private') is True: return 'private'
    if raw.get('disabled') is True: return 'disabled'
    if _is_safety_filtered(repo,tags): return 'safety_filtered'
    normalized={str(x).casefold() for x in tags if isinstance(x,str)}
    if 'gguf' not in normalized: return 'not_gguf'
    if raw.get('pipeline_tag')!='text-generation' and 'text-generation' not in normalized and 'conversational' not in normalized: return 'not_text_generation'
    return 'active'

def _validated_metadata(raw,repo):
    tags=raw.get('tags')
    if not isinstance(tags,list) or not all(isinstance(x,str) for x in tags): raise CatalogSyncError(f'Niekompletne tagi repozytorium {repo}; zachowano poprzedni katalog')
    revision,modified=raw.get('sha'),raw.get('lastModified')
    if not isinstance(revision,str) or not _SHA.fullmatch(revision): raise CatalogSyncError(f'Niekompletna rewizja repozytorium {repo}; zachowano poprzedni katalog')
    if not isinstance(modified,str) or not modified: raise CatalogSyncError(f'Brak daty modyfikacji repozytorium {repo}; zachowano poprzedni katalog')
    try: datetime.fromisoformat(modified.replace('Z','+00:00'))
    except ValueError as exc: raise CatalogSyncError(f'Nieprawidłowa data modyfikacji repozytorium {repo}; zachowano poprzedni katalog') from exc
    safe=sorted({x[:120] for x in tags if len(x)<=120}); licenses=[x.split(':',1)[1] for x in safe if x.startswith('license:')]
    return {'revision':revision,'last_modified':modified,'tags':safe,'declared_license_tag':licenses[0] if licenses else None,'pipeline_tag':raw.get('pipeline_tag') if raw.get('pipeline_tag')=='text-generation' else ('text-generation' if 'text-generation' in safe else 'conversational'),'gated':raw.get('gated') is True}

def _seed_existing(item,now):
    audit=item.get('license_audit') or {}
    return {'catalog_id':f"arena:{item['model_id']}",'arena_model_id':item['model_id'],'display_name':item['repo_id'].rsplit('/',1)[-1],
            'repo_id':item['repo_id'],'audited_revision':item['revision'],'revision':None,'last_modified':None,
            'discovered_at':now,'last_verified_at':None,'source_status':'not_checked','metadata_status':'not_checked',
            'license_review_status':audit.get('status',item.get('license_review_status','review_required')),
            'license_verified':False,'technical_readiness':'not_evaluated','admission_status':'existing_registry_entry','tags':[],
            'history':[{'at':now,'event':'seeded_from_pinned_manifest','revision':item['revision']}]}

def _new_candidate(repo,metadata,now):
    return {'catalog_id':'hf:'+repo,'arena_model_id':None,'display_name':repo.rsplit('/',1)[-1],'repo_id':repo,'audited_revision':None,
            **metadata,'discovered_at':now,'last_verified_at':now,'source_status':'active','metadata_status':'review_required',
            'license_review_status':'review_required','license_verified':False,'technical_readiness':'not_evaluated',
            'admission_status':'not_admitted','history':[{'at':now,'event':'discovered','revision':metadata['revision']}]}

def _history(entry,event,now,**details):
    hist=list(entry.get('history') or []); hist.append({'at':now,'event':event,**details}); entry['history']=hist

def _reset_review(entry,safety=False):
    entry['license_review_status']='review_required'; entry['license_verified']=False; entry['technical_readiness']='not_evaluated'
    if safety: entry['admission_status']='blocked'
    elif entry.get('arena_model_id') is None: entry['admission_status']='not_admitted'

def _mark_source_state(entry,status,now,**details):
    if entry.get('source_status')==status and all(entry.get(k)==v for k,v in details.items()): return False
    entry['source_status']=status; entry['metadata_status']='review_required'; entry['last_verified_at']=now; entry.update(details)
    _reset_review(entry,safety=(status=='safety_filtered')); _history(entry,status,now,**details); return True

def _update_entry(entry,metadata,now,source_item=None):
    fields=('revision','last_modified','tags','declared_license_tag','pipeline_tag','gated')
    old_revision=entry.get('revision'); old_license=entry.get('declared_license_tag')
    changed=any(entry.get(k)!=metadata.get(k) for k in fields)
    if source_item is not None:
        audited=source_item['revision']; audit_status=source_item.get('license_review_status','review_required')
        if entry.get('audited_revision')!=audited:
            entry['audited_revision']=audited; changed=True; _history(entry,'pinned_manifest_changed',now,audited_revision=audited)
    else: audited=entry.get('audited_revision'); audit_status='review_required'
    if changed:
        entry.update(metadata); entry['last_verified_at']=now
        revision_changed=(old_revision is not None and old_revision!=metadata['revision']) or (entry.get('arena_model_id') is not None and metadata['revision']!=audited)
        license_changed=old_license is not None and old_license!=metadata.get('declared_license_tag')
        entry['metadata_status']='review_required' if revision_changed else 'metadata_changed'
        if entry.get('arena_model_id') is None or revision_changed or license_changed: _reset_review(entry)
        else: entry['license_review_status']=audit_status if metadata['revision']==audited else 'review_required'; entry['license_verified']=False; entry['technical_readiness']='not_evaluated'
        if revision_changed: entry['license_review_status']='review_required'; _history(entry,'revision_changed',now,previous_revision=old_revision,revision=metadata['revision'])
        else: _history(entry,'metadata_changed',now,revision=metadata['revision'])
    elif source_item is not None:
        desired=audit_status if entry.get('revision')==audited else 'review_required'
        if entry.get('license_review_status')!=desired:
            entry['license_review_status']=desired; entry['license_verified']=False; entry['last_verified_at']=now; _history(entry,'license_audit_status_changed',now,status=desired); changed=True
    if entry.get('source_status')!='active': entry['source_status']='active'; entry['last_verified_at']=now; _history(entry,'source_reappeared',now,revision=metadata['revision']); changed=True
    return changed

def _load_previous(path,source_models,now):
    try: previous=json.loads(Path(path).read_text(encoding='utf-8'))
    except FileNotFoundError: previous=None
    except (OSError,json.JSONDecodeError) as exc: raise CatalogSyncError('Istniejący katalog kandydatów jest uszkodzony; odmowa nadpisania') from exc
    if previous is None: return None,{x['repo_id']:_seed_existing(x,now) for x in source_models}
    if not isinstance(previous,dict) or previous.get('schema_version')!=1 or not isinstance(previous.get('models'),list): raise CatalogSyncError('Nieobsługiwana lub uszkodzona wersja istniejącego katalogu')
    # Migrate the previous IDs-1–8-only schema without dropping its history.
    if previous['models'] and 'catalog_id' not in previous['models'][0] and 'model_id' in previous['models'][0]:
        old_by_id={x.get('model_id'):x for x in previous['models'] if isinstance(x,dict)}
        if sorted(old_by_id)!=list(range(1,9)): raise CatalogSyncError('Stary katalog ma nieprawidłowe ID; odmowa nadpisania')
        by_repo={}
        for source in source_models:
            old=old_by_id[source['model_id']]; item=_seed_existing(source,now)
            item.update({'revision':old.get('current_revision'),'last_modified':old.get('last_modified'),
                         'discovered_at':old.get('first_seen_at',now),'last_verified_at':old.get('last_checked_at'),
                         'source_status':old.get('source_status','not_checked'),'metadata_status':old.get('metadata_status','not_checked'),
                         'license_review_status':old.get('license_review_status',source.get('license_review_status','review_required')),
                         'history':list(old.get('history') or [])})
            by_repo[source['repo_id']]=item
        return None,by_repo
    by_repo={}; seen_ids=set()
    for item in previous['models']:
        if not isinstance(item,dict) or not isinstance(item.get('repo_id'),str) or not _REPO.fullmatch(item['repo_id']): raise CatalogSyncError('Uszkodzony wpis istniejącego katalogu; odmowa nadpisania')
        cid=item.get('catalog_id')
        if item['repo_id'] in by_repo or not isinstance(cid,str) or cid in seen_ids: raise CatalogSyncError('Powtórzony repo_id/catalog_id w istniejącym katalogu')
        by_repo[item['repo_id']]=dict(item); seen_ids.add(cid)
    for source in source_models: by_repo.setdefault(source['repo_id'],_seed_existing(source,now))
    if len(by_repo)>MAX_TRACKED_CANDIDATES: raise CatalogSyncError('Katalog przekracza bezpieczny limit śledzonych repozytoriów')
    return previous,by_repo

def _verify_existing(entry,source,fetcher,now):
    repo=source['repo_id']; url=API_ROOT+'/'+urllib.parse.quote(repo,safe='/')
    try: data,final=fetcher(url)
    except HTTPStatusError as exc:
        if exc.status in (404,410): return _mark_source_state(entry,'missing',now),'missing'
        raise CatalogSyncError(f'Nie udało się zweryfikować istniejącego modelu {repo}; zachowano poprzedni katalog') from exc
    moved=_repo_from_api_url(final)
    if moved and moved!=repo: return _mark_source_state(entry,'moved',now,moved_to_repo_id=moved),'moved'
    if not isinstance(data,dict): raise CatalogSyncError(f'Niekompletne szczegóły repozytorium {repo}; zachowano poprzedni katalog')
    tags=data.get('tags')
    if not isinstance(tags,list): raise CatalogSyncError(f'Niekompletne tagi repozytorium {repo}; zachowano poprzedni katalog')
    status=_source_status(data,repo,tags)
    if status!='active': return _mark_source_state(entry,status,now),status
    return _update_entry(entry,_validated_metadata(data,repo),now,source),None

def sync_catalog(source_path=SOURCE_MANIFEST,catalog_path=CATALOG_PATH,fetcher=fetch_json,now=None):
    now=now or utc_now(); source_models=_load_source_models(Path(source_path)); previous,by_repo=_load_previous(Path(catalog_path),source_models,now)
    counts={'new':0,'changed':0,'missing':0,'moved':0,'reappeared':0,'filtered_by_reason':{},'source_models_checked':0}
    source_by_repo={x['repo_id']:x for x in source_models}
    for source in source_models:
        repo=source['repo_id']; entry=by_repo[repo]; before=entry.get('source_status')
        changed,state=_verify_existing(entry,source,fetcher,now); counts['source_models_checked']+=1
        if changed: counts['changed']+=1
        if state:
            counts['filtered_by_reason'][state]=counts['filtered_by_reason'].get(state,0)+1
            if state=='missing': counts['missing']+=1
            elif state=='moved': counts['moved']+=1
        if before!='active' and entry.get('source_status')=='active': counts['reappeared']+=1
    discovered={}; raw_counts={}
    for sort in ('lastModified','downloads'):
        payload,_=fetcher(_list_url(sort))
        if not isinstance(payload,list) or not payload: raise CatalogSyncError(f'Pusta lub nieprawidłowa odpowiedź katalogu ({sort}); zachowano poprzedni manifest')
        raw_counts[sort]=len(payload)
        for raw in payload:
            if not isinstance(raw,dict): raise CatalogSyncError('Nieprawidłowy rekord z API; zachowano poprzedni katalog')
            repo,tags=raw.get('id'),raw.get('tags')
            if not isinstance(repo,str) or not _REPO.fullmatch(repo): raise CatalogSyncError('Nieprawidłowy identyfikator repozytorium z API')
            if not isinstance(tags,list) or not all(isinstance(x,str) for x in tags): raise CatalogSyncError(f'Niekompletne tagi repozytorium {repo}; zachowano poprzedni katalog')
            status=_source_status(raw,repo,tags)
            if status!='active':
                counts['filtered_by_reason'][status]=counts['filtered_by_reason'].get(status,0)+1
                if repo in by_repo and repo not in source_by_repo and _mark_source_state(by_repo[repo],status,now): counts['changed']+=1
                continue
            metadata=_validated_metadata(raw,repo)
            if repo in discovered and discovered[repo]['revision']!=metadata['revision']: raise CatalogSyncError(f'Niespójne rewizje repozytorium {repo} w odpowiedziach API; zachowano katalog')
            discovered[repo]=metadata
    for repo,metadata in discovered.items():
        if repo in source_by_repo: continue
        prior=by_repo.get(repo)
        if prior is None: by_repo[repo]=_new_candidate(repo,metadata,now); counts['new']+=1
        else:
            before=prior.get('source_status')
            if _update_entry(prior,metadata,now): counts['changed']+=1
            if before!='active' and prior.get('source_status')=='active': counts['reappeared']+=1
    for repo,entry in list(by_repo.items()):
        if repo in source_by_repo or repo in discovered or entry.get('source_status')!='active': continue
        url=API_ROOT+'/'+urllib.parse.quote(repo,safe='/')
        try: detail,final=fetcher(url)
        except HTTPStatusError as exc:
            if exc.status in (404,410):
                if _mark_source_state(entry,'missing',now): counts['changed']+=1; counts['missing']+=1
                continue
            raise CatalogSyncError(f'Nie udało się zweryfikować repozytorium {repo} (HTTP {exc.status}); zachowano poprzedni katalog') from exc
        moved=_repo_from_api_url(final)
        if moved and moved!=repo:
            if _mark_source_state(entry,'moved',now,moved_to_repo_id=moved): counts['changed']+=1; counts['moved']+=1
            continue
        if not isinstance(detail,dict): raise CatalogSyncError(f'Niekompletne szczegóły repozytorium {repo}; zachowano poprzedni katalog')
        tags=detail.get('tags')
        if not isinstance(tags,list): raise CatalogSyncError(f'Niekompletne tagi repozytorium {repo}; zachowano poprzedni katalog')
        status=_source_status(detail,repo,tags)
        if status!='active':
            if _mark_source_state(entry,status,now): counts['changed']+=1; counts['filtered_by_reason'][status]=counts['filtered_by_reason'].get(status,0)+1
            continue
        if _update_entry(entry,_validated_metadata(detail,repo),now): counts['changed']+=1
    if len(by_repo)>MAX_TRACKED_CANDIDATES: raise CatalogSyncError('Nowy katalog przekroczył limit; poprzedni manifest pozostaje bez zmian')
    models=sorted(by_repo.values(),key=lambda x:x['repo_id'].casefold())
    catalog={'schema_version':1,'provider':'Hugging Face','scope':'Informacyjny katalog publicznych repozytoriów GGUF text-generation/conversational; wpis nie oznacza dopuszczenia do turnieju.',
             'query_scope':{'filter':'gguf','accepted_task_tags':['text-generation','conversational'],'sorts':['lastModified','downloads'],'limit_per_query':LIMIT_PER_QUERY,
                            'safety_filter':'model_exclusions.py plus conservative repository/tag deny terms'},
             'weights_downloaded':False,'inference_run':False,'models':models}
    if any(counts[k] for k in ('new','changed','missing','moved','reappeared')): catalog['last_catalog_change_at']=now
    elif isinstance(previous,dict) and 'last_catalog_change_at' in previous: catalog['last_catalog_change_at']=previous['last_catalog_change_at']
    report={'status':'success','checked_at':now,'queries':raw_counts,'tracked_models':len(models),**counts,'weights_downloaded':False,'inference_run':False,'catalog_changed':catalog!=previous}
    return catalog,report

def _atomic_json(path,payload):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True); fd,tmp=tempfile.mkstemp(prefix=path.name+'.',suffix='.tmp',dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as f: json.dump(payload,f,ensure_ascii=False,indent=2,sort_keys=True); f.write('\n'); f.flush(); os.fsync(f.fileno())
        os.chmod(tmp,0o644)
        os.replace(tmp,path)
    except Exception:
        try: os.unlink(tmp)
        except FileNotFoundError: pass
        raise

def run_sync(source_path=SOURCE_MANIFEST,catalog_path=CATALOG_PATH,report_path=REPORT_PATH,fetcher=fetch_json,now=None):
    try:
        catalog,report=sync_catalog(source_path,catalog_path,fetcher,now)
        if report['catalog_changed']: _atomic_json(catalog_path,catalog)
        _atomic_json(report_path,report); return report
    except Exception as exc:
        failure={'status':'error','checked_at':now or utc_now(),'error_type':type(exc).__name__,'error':str(exc),'manifest_preserved':True,'weights_downloaded':False,'inference_run':False}
        _atomic_json(report_path,failure)
        if isinstance(exc,CatalogSyncError): raise
        raise CatalogSyncError('Synchronizacja nie powiodła się; zachowano poprzedni katalog') from exc
