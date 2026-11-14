"""Metadata-only checks for the eight existing AI Chess Arena model repositories."""
from __future__ import annotations
import json, os, tempfile, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parent
SOURCE_MANIFEST=ROOT/'manifests/huggingface-models.json'
CATALOG_PATH=ROOT/'manifests/huggingface-candidates.json'
REPORT_PATH=ROOT/'reports/huggingface-catalog-sync.json'
MODEL_IDS=tuple(range(1,9))
API_ROOT='https://huggingface.co/api/models/'
class CatalogSyncError(RuntimeError): pass
class HTTPStatusError(CatalogSyncError):
 def __init__(self,status,url): super().__init__(f'Hugging Face API HTTP {status}'); self.status=status; self.url=url
def utc_now(): return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00','Z')
def fetch_json(url,timeout=20):
 req=urllib.request.Request(url,headers={'User-Agent':'AI-Chess-Arena-Existing-Models-Metadata-Check/1.0','Accept':'application/json'})
 try:
  with urllib.request.urlopen(req,timeout=timeout) as response: return json.load(response),response.geturl()
 except urllib.error.HTTPError as exc: raise HTTPStatusError(exc.code,url) from exc
 except (urllib.error.URLError,TimeoutError,json.JSONDecodeError) as exc: raise CatalogSyncError(f'Hugging Face metadata read failed: {type(exc).__name__}') from exc
def _repo_from_api_url(url):
 parsed=urllib.parse.urlsplit(url); prefix='/api/models/'
 if parsed.netloc.lower()=='huggingface.co' and parsed.path.startswith(prefix): return urllib.parse.unquote(parsed.path[len(prefix):]).strip('/')
 return None
def _load_source(path):
 try: source=json.loads(Path(path).read_text(encoding='utf-8'))
 except (OSError,json.JSONDecodeError) as exc: raise CatalogSyncError('Cannot read pinned source manifest') from exc
 models=source.get('models') if isinstance(source,dict) else None
 if not isinstance(models,list): raise CatalogSyncError('Source manifest has no models list')
 selected=sorted((x for x in models if isinstance(x,dict) and x.get('model_id') in MODEL_IDS),key=lambda x:x['model_id'])
 if [x.get('model_id') for x in selected]!=list(MODEL_IDS): raise CatalogSyncError('Source manifest must contain exactly model IDs 1–8')
 for x in selected:
  rev=x.get('revision'); repo=x.get('repo_id')
  if not isinstance(repo,str) or '/' not in repo or not isinstance(rev,str) or len(rev)!=40 or any(c not in '0123456789abcdef' for c in rev): raise CatalogSyncError('Invalid repo_id or pinned revision in source manifest')
 return selected
def _seed(item,now):
 audit=item.get('license_audit') or {}
 return {'model_id':item['model_id'],'display_name':item['repo_id'].rsplit('/',1)[-1],'repo_id':item['repo_id'],'audited_revision':item['revision'],'current_revision':None,'last_modified':None,'first_seen_at':now,'last_checked_at':None,'source_status':'not_checked','metadata_status':'not_checked','license_review_status':audit.get('status',item.get('license_review_status','review_required')),'history':[]}
def _history(entry,event,now,**details):
 hist=list(entry.get('history') or []); hist.append({'at':now,'event':event,**details}); entry['history']=hist[-30:]
def _atomic_json(path,payload):
 path=Path(path); path.parent.mkdir(parents=True,exist_ok=True); fd,tmp=tempfile.mkstemp(prefix=path.name+'.',suffix='.tmp',dir=path.parent)
 try:
  with os.fdopen(fd,'w',encoding='utf-8') as f: json.dump(payload,f,ensure_ascii=False,indent=2,sort_keys=True); f.write('\n'); f.flush(); os.fsync(f.fileno())
  os.replace(tmp,path)
 except Exception:
  try: os.unlink(tmp)
  except FileNotFoundError: pass
  raise
def sync_catalog(source_path=SOURCE_MANIFEST,catalog_path=CATALOG_PATH,fetcher=fetch_json,now=None):
 now=now or utc_now(); source_models=_load_source(source_path)
 try: previous=json.loads(Path(catalog_path).read_text(encoding='utf-8'))
 except FileNotFoundError: previous=None
 except (OSError,json.JSONDecodeError) as exc: raise CatalogSyncError('Existing catalog is corrupt; refusing to overwrite') from exc
 if previous is None: by_id={x['model_id']:_seed(x,now) for x in source_models}
 else:
  if not isinstance(previous,dict) or previous.get('schema_version')!=1: raise CatalogSyncError('Unsupported existing catalog schema')
  old=previous.get('models')
  if not isinstance(old,list) or [x.get('model_id') for x in old if isinstance(x,dict)]!=list(MODEL_IDS): raise CatalogSyncError('Existing catalog must contain exactly IDs 1–8')
  by_id={x['model_id']:dict(x) for x in old}
 counts={'unchanged':0,'changed':0,'missing':0,'moved':0,'restricted':0}
 for source in source_models:
  mid=source['model_id']; entry=by_id[mid]; repo=source['repo_id']
  if entry.get('repo_id')!=repo: raise CatalogSyncError(f'repo_id change for model ID {mid} requires human review')
  url=API_ROOT+urllib.parse.quote(repo,safe='/')
  try: data,final_url=fetcher(url)
  except HTTPStatusError as exc:
   if exc.status not in (404,410): raise CatalogSyncError(f'Could not check model ID {mid}; previous catalog preserved') from exc
   if entry.get('source_status')!='missing': _history(entry,'repository_missing',now,previous_revision=entry.get('current_revision')); entry['last_checked_at']=now
   entry.update({'source_status':'missing','metadata_status':'review_required','license_review_status':'review_required'}); counts['missing']+=1; continue
  moved=_repo_from_api_url(final_url)
  if moved and moved!=repo:
   if entry.get('source_status')!='moved' or entry.get('moved_to_repo_id')!=moved: _history(entry,'repository_moved',now,destination_repo_id=moved); entry['last_checked_at']=now
   entry.update({'source_status':'moved','metadata_status':'review_required','license_review_status':'review_required','moved_to_repo_id':moved}); counts['moved']+=1; continue
  if not isinstance(data,dict): raise CatalogSyncError(f'Incomplete metadata for model ID {mid}; previous catalog preserved')
  revision=data.get('sha'); modified=data.get('lastModified')
  if not isinstance(revision,str) or len(revision)!=40 or any(c not in '0123456789abcdef' for c in revision): raise CatalogSyncError(f'Incomplete revision for model ID {mid}; previous catalog preserved')
  if not isinstance(modified,str) or not modified: raise CatalogSyncError(f'Missing lastModified for model ID {mid}; previous catalog preserved')
  try: datetime.fromisoformat(modified.replace('Z','+00:00'))
  except ValueError as exc: raise CatalogSyncError(f'Invalid lastModified for model ID {mid}; previous catalog preserved') from exc
  if data.get('private') is True or data.get('disabled') is True or data.get('gated') is True:
   status='private' if data.get('private') is True else 'disabled' if data.get('disabled') is True else 'gated'
   if entry.get('source_status')!=status or entry.get('current_revision')!=revision: _history(entry,'source_restricted',now,source_status=status,revision=revision); entry['last_checked_at']=now
   entry.update({'source_status':status,'metadata_status':'review_required','license_review_status':'review_required','current_revision':revision,'last_modified':modified}); counts['restricted']+=1; continue
  previous_revision=entry.get('current_revision') or entry.get('audited_revision')
  if previous_revision!=revision:
   _history(entry,'revision_changed',now,previous_revision=previous_revision,current_revision=revision,audited_revision=entry.get('audited_revision')); entry['last_checked_at']=now; counts['changed']+=1
  elif entry.get('source_status')!='active': _history(entry,'source_restored',now,revision=revision); entry['last_checked_at']=now
  elif entry.get('last_checked_at') is None: entry['last_checked_at']=now
  matches_audit=revision==entry.get('audited_revision')
  entry.update({'source_status':'active','metadata_status':'unchanged' if matches_audit else 'review_required','current_revision':revision,'last_modified':modified})
  entry.pop('moved_to_repo_id',None)
  if not matches_audit: entry['license_review_status']='review_required'
  if previous_revision==revision: counts['unchanged']+=1
 catalog={'schema_version':1,'provider':'Hugging Face','scope':'Exactly existing model IDs 1–8 from manifests/huggingface-models.json; no discovery of new repositories.','weights_downloaded':False,'inference_run':False,'models':[by_id[i] for i in MODEL_IDS]}
 report={'status':'success','checked_at':now,'scope_model_ids':list(MODEL_IDS),**counts,'weights_downloaded':False,'inference_run':False,'catalog_changed':catalog!=previous}
 return catalog,report
def run_sync(source_path=SOURCE_MANIFEST,catalog_path=CATALOG_PATH,report_path=REPORT_PATH,fetcher=fetch_json,now=None):
 try:
  catalog,report=sync_catalog(source_path,catalog_path,fetcher,now)
  if report['catalog_changed']: _atomic_json(catalog_path,catalog)
  _atomic_json(report_path,report); return report
 except Exception as exc:
  failure={'status':'error','checked_at':now or utc_now(),'error_type':type(exc).__name__,'error':str(exc),'manifest_preserved':True,'weights_downloaded':False,'inference_run':False}
  _atomic_json(report_path,failure)
  if isinstance(exc,CatalogSyncError): raise
  raise CatalogSyncError('Sync failed; previous catalog preserved') from exc
