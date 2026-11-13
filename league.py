import hashlib,json,re,shutil,sqlite3,time
from pathlib import Path
ROOT=Path(__file__).resolve().parent
DB=ROOT/'arena.sqlite3'
MODEL_DIR=ROOT/'managed-models'
INITIAL_ELO=1200.0
def connect():
 c=sqlite3.connect(DB,timeout=10); c.row_factory=sqlite3.Row; c.execute('PRAGMA foreign_keys=ON'); return c
def init():
 with connect() as c:
  c.executescript("""
  CREATE TABLE IF NOT EXISTS league_models(id INTEGER PRIMARY KEY,name TEXT NOT NULL UNIQUE,format TEXT NOT NULL DEFAULT 'PURE_AI',active INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,notes TEXT NOT NULL DEFAULT '');
  CREATE TABLE IF NOT EXISTS model_versions(id INTEGER PRIMARY KEY,model_id INTEGER NOT NULL REFERENCES league_models(id),version_key TEXT NOT NULL,filename TEXT,sha256 TEXT,size_bytes INTEGER,source TEXT NOT NULL DEFAULT '',license TEXT NOT NULL DEFAULT '',metadata_json TEXT NOT NULL DEFAULT '{}',runtime TEXT NOT NULL DEFAULT 'llamafile',status TEXT NOT NULL DEFAULT 'catalogued',managed_copy INTEGER NOT NULL DEFAULT 0,license_verified INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL,UNIQUE(model_id,version_key));
  CREATE TABLE IF NOT EXISTS tournaments(id INTEGER PRIMARY KEY,name TEXT NOT NULL,format TEXT NOT NULL DEFAULT 'PURE_AI',status TEXT NOT NULL DEFAULT 'planned',created_at TEXT NOT NULL,rules_version TEXT NOT NULL DEFAULT '1');
  CREATE TABLE IF NOT EXISTS tournament_entries(tournament_id INTEGER NOT NULL REFERENCES tournaments(id),model_id INTEGER NOT NULL REFERENCES league_models(id),version_id INTEGER REFERENCES model_versions(id),PRIMARY KEY(tournament_id,model_id));
  CREATE TABLE IF NOT EXISTS tournament_pairings(id INTEGER PRIMARY KEY,tournament_id INTEGER NOT NULL REFERENCES tournaments(id),white_model_id INTEGER NOT NULL REFERENCES league_models(id),black_model_id INTEGER NOT NULL REFERENCES league_models(id),leg INTEGER NOT NULL CHECK(leg IN (1,2)),status TEXT NOT NULL DEFAULT 'pending',game_id INTEGER,UNIQUE(tournament_id,white_model_id,black_model_id,leg));
  CREATE INDEX IF NOT EXISTS idx_pairings_tournament ON tournament_pairings(tournament_id,status);

  """)
  cols={r[1] for r in c.execute('PRAGMA table_info(model_versions)')}
  if 'license_verified' not in cols:
   c.execute('ALTER TABLE model_versions ADD COLUMN license_verified INTEGER NOT NULL DEFAULT 0')
  for name, declaration in (('draft_filename','TEXT'),('draft_sha256','TEXT'),('draft_size_bytes','INTEGER'),('draft_status',"TEXT NOT NULL DEFAULT 'catalogued'"),('draft_managed_copy','INTEGER NOT NULL DEFAULT 0'),('draft_download_url','TEXT'),('draft_source_path','TEXT')):
   if name not in cols:
    c.execute(f'ALTER TABLE model_versions ADD COLUMN {name} {declaration}')
def register_model(name,filename=None,source='',license='',metadata=None,format='PURE_AI',license_verified=False):
 if not name or format not in ('PURE_AI','AI_ENGINE'): raise ValueError('Nieprawidłowa nazwa lub format')
 if license_verified:
  normalized_license=(license or '').strip().lower()
  if not (source or '').strip().lower().startswith('https://'):
   raise ValueError('Zatwierdzona licencja wymaga źródła HTTPS')
  if not normalized_license or normalized_license in ('nieustalona','unknown','n/a','unverified','unspecified','none'):
   raise ValueError('Nie można zatwierdzić nieustalonej licencji')
 metadata = dict(metadata or {})
 draft = metadata.get('draft_model')
 if draft is not None:
  if not isinstance(draft, dict): raise ValueError('Nieprawidłowy manifest modelu draft')
  draft_filename = draft.get('filename')
  draft_sha = draft.get('sha256')
  draft_size = draft.get('size_bytes')
  draft_url = draft.get('download_url')
  if not isinstance(draft_filename,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._+-]{0,180}\.gguf',draft_filename): raise ValueError('Nieprawidłowa nazwa pliku draft GGUF')
  if not isinstance(draft_sha,str) or not re.fullmatch(r'[0-9a-fA-F]{64}',draft_sha): raise ValueError('Nieprawidłowy SHA-256 pliku draft')
  if not isinstance(draft_size,int) or draft_size<=0: raise ValueError('Nieprawidłowy rozmiar pliku draft')
  if not isinstance(draft_url,str) or not draft_url.startswith('https://'): raise ValueError('Plik draft wymaga źródła HTTPS')
  if not isinstance(draft.get('source_path'),str) or not draft['source_path'] or any(part in ('.','..') for part in draft['source_path'].split('/')): raise ValueError('Brak lub nieprawidłowa ścieżka źródłowa pliku draft')
  if draft_filename == metadata.get('filename'): raise ValueError('Target i draft muszą wskazywać różne pliki')
 # A catalog entry carries an immutable remote manifest; the GGUF itself is
 # optional and is fetched into the private cache only when a game needs it.
 if filename is None:
  filename = metadata.get('filename')
 digest = metadata.get('sha256')
 size = metadata.get('size_bytes')
 if filename is not None and (not isinstance(filename,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._+-]{0,180}\.gguf',filename)):
  raise ValueError('Wymagana bezpieczna nazwa pliku GGUF')
 if digest is not None and (not isinstance(digest,str) or not re.fullmatch(r'[0-9a-fA-F]{64}',digest)):
  raise ValueError('Nieprawidłowy SHA-256 manifestu modelu')
 if size is not None and (not isinstance(size,int) or size <= 0):
  raise ValueError('Nieprawidłowy rozmiar manifestu modelu')
 p=(MODEL_DIR/filename).resolve() if filename else None; status='catalogued'
 if p:
  if p.parent!=MODEL_DIR.resolve() or p.suffix.lower()!='.gguf': raise ValueError('Nieprawidłowa ścieżka GGUF')
  if p.is_file():
   actual_size=p.stat().st_size; h=hashlib.sha256()
   with p.open('rb') as f:
    for chunk in iter(lambda:f.read(1048576),b''): h.update(chunk)
   actual_digest=h.hexdigest()
   if size is not None and actual_size != size or digest is not None and actual_digest.lower() != digest.lower(): raise ValueError('Lokalny GGUF nie zgadza się z manifestem SHA-256/rozmiaru')
   size=actual_size; digest=actual_digest; status='downloaded'
 if any(value is not None for value in (filename,digest,size)) and not all(value is not None for value in (filename,digest,size)):
  raise ValueError('Manifest modelu musi zawierać nazwę pliku, rozmiar i SHA-256')
 # Different repositories often publish a generic basename such as model.gguf.
 # Keep the original upstream path in metadata, but make the local cache name
 # content-specific whenever that basename is already assigned to other bytes.
 if filename and digest:
  with connect() as c:
   collision=c.execute('SELECT 1 FROM model_versions WHERE filename=? AND sha256<>? LIMIT 1',(filename,digest)).fetchone()
  if collision:
   metadata.setdefault('source_path', filename)
   stem=Path(filename).stem
   filename=f'{stem}-{digest[:16]}.gguf'
 # Cache paths are unique per catalog entry/configuration. This prevents a
 # cleanup of a standalone target from deleting a shared file used by a pair.
 config_key = hashlib.sha256(f"{digest or ''}:{draft_sha if draft else ''}".encode()).hexdigest()[:16]
 if filename:
  with connect() as c:
   collision=c.execute('SELECT 1 FROM model_versions WHERE filename IN (?,?) OR draft_filename IN (?,?) LIMIT 1',(filename,filename,filename,filename)).fetchone()
  if collision:
   metadata.setdefault('source_path', metadata.get('filename') or filename)
   filename=f'{Path(filename).stem}-{config_key}.gguf'
 if draft:
  with connect() as c:
   collision=c.execute('SELECT 1 FROM model_versions WHERE filename=? OR draft_filename=? LIMIT 1',(draft_filename,draft_filename)).fetchone()
  if collision:
   draft_filename=f'{Path(draft_filename).stem}-{config_key}.gguf'
 now=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())
 with connect() as c:
  c.execute('INSERT OR IGNORE INTO league_models(name,format,created_at) VALUES(?,?,?)',(name,format,now)); m=c.execute('SELECT id,format FROM league_models WHERE name=?',(name,)).fetchone()
  if m['format']!=format: raise ValueError('Model istnieje w innym formacie')
  version_key=(config_key if draft else digest) if digest else (filename or ('catalog:'+name))
  c.execute('''INSERT OR IGNORE INTO model_versions(model_id,version_key,filename,sha256,size_bytes,source,license,metadata_json,status,license_verified,created_at,draft_filename,draft_sha256,draft_size_bytes,draft_download_url,draft_source_path) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(m['id'],version_key,filename,digest,size,source,license,json.dumps(metadata,ensure_ascii=False),status,int(bool(license_verified and source and license)),now,draft_filename if draft else None,draft.get('sha256') if draft else None,draft.get('size_bytes') if draft else None,draft.get('download_url') if draft else None,draft.get('source_path') if draft else None))
  vs=c.execute('SELECT * FROM model_versions WHERE model_id=? ORDER BY id DESC',(m['id'],)).fetchall()
  return {'id':m['id'],'name':name,'format':format,'versions':[dict(v) for v in vs]}
def create_round_robin(name,format='PURE_AI'):
 with connect() as c:
  candidates=c.execute('''SELECT m.id,
      (SELECT v.id FROM model_versions v WHERE v.model_id=m.id AND v.license_verified=1
       AND v.source<>'' AND v.filename IS NOT NULL AND v.sha256 IS NOT NULL AND v.size_bytes>0
       ORDER BY v.id DESC LIMIT 1) AS version_id,
      (SELECT v.sha256 FROM model_versions v WHERE v.model_id=m.id AND v.license_verified=1
       AND v.source<>'' AND v.filename IS NOT NULL AND v.sha256 IS NOT NULL AND v.size_bytes>0
       ORDER BY v.id DESC LIMIT 1) AS model_sha256,
      (SELECT COALESCE(v.draft_sha256,'') FROM model_versions v WHERE v.model_id=m.id AND v.license_verified=1
       AND v.source<>'' AND v.filename IS NOT NULL AND v.sha256 IS NOT NULL AND v.size_bytes>0
       ORDER BY v.id DESC LIMIT 1) AS draft_sha256
      FROM league_models m WHERE m.active=1 AND m.format=?
      AND EXISTS(SELECT 1 FROM model_versions v WHERE v.model_id=m.id AND v.license_verified=1
       AND v.source<>'' AND v.filename IS NOT NULL AND v.sha256 IS NOT NULL AND v.size_bytes>0)
      ORDER BY m.id''',(format,)).fetchall()
  # Aliases or separately registered entries with identical weights are one
  # competitor, not a meaningful self-match. Keep the first catalog entry.
  ms=[]; seen_hashes=set()
  for candidate in candidates:
   # A target with and without a draft head is a distinct runtime configuration.
   identity=(candidate['model_sha256'], candidate['draft_sha256'] or '')
   if identity in seen_hashes: continue
   seen_hashes.add(identity); ms.append(candidate)
  if len(ms)<2: raise ValueError('Potrzeba co najmniej dwóch różnych modeli lub konfiguracji target/draft z jawnie zweryfikowaną licencją')
  now=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()); tid=c.execute('INSERT INTO tournaments(name,format,created_at) VALUES(?,?,?)',(name,format,now)).lastrowid
  for m in ms:
   c.execute('INSERT INTO tournament_entries(tournament_id,model_id,version_id) VALUES(?,?,?)',(tid,m['id'],m['version_id']))
  for i,a in enumerate(ms):
   for b in ms[i+1:]:
    c.execute('INSERT INTO tournament_pairings(tournament_id,white_model_id,black_model_id,leg) VALUES(?,?,?,1)',(tid,a['id'],b['id']))
    c.execute('INSERT INTO tournament_pairings(tournament_id,white_model_id,black_model_id,leg) VALUES(?,?,?,2)',(tid,b['id'],a['id']))
  return {'id':tid,'name':name,'format':format,'players':len(ms),'games_planned':len(ms)*(len(ms)-1),'status':'planned'}
def elo_ratings(format='PURE_AI',k=32.0):
 with connect() as c:
  ps=c.execute('SELECT id,name FROM league_models WHERE active=1 AND format=? ORDER BY name',(format,)).fetchall(); ratings={p['id']:INITIAL_ELO for p in ps}; stats={p['id']:{'wins':0,'draws':0,'losses':0,'games':0,'score':0.0} for p in ps}
  rows=c.execute('SELECT g.white_model,g.black_model,g.result FROM games g JOIN league_models w ON w.name=g.white_model JOIN league_models b ON b.name=g.black_model WHERE w.format=? AND b.format=? ORDER BY g.id',(format,format)).fetchall(); ids={p['name']:p['id'] for p in ps}
  for g in rows:
   w=ids.get(g['white_model']); b=ids.get(g['black_model'])
   if w is None or b is None: continue
   exp=1/(1+10**((ratings[b]-ratings[w])/400)); act=1.0 if g['result']=='1-0' else 0.0 if g['result']=='0-1' else .5; d=k*(act-exp); ratings[w]+=d; ratings[b]-=d
   for pid,score in ((w,act),(b,1-act)):
    s=stats[pid]; s['games']+=1; s['score']+=score
    if score==1:s['wins']+=1
    elif score==.5:s['draws']+=1
    else:s['losses']+=1
  return [dict(name=p['name'],elo=round(ratings[p['id']]),**stats[p['id']],win_rate=round(100*stats[p['id']]['score']/stats[p['id']]['games'],1) if stats[p['id']]['games'] else 0.0) for p in sorted(ps,key=lambda p:ratings[p['id']],reverse=True)]
def storage_status():
 MODEL_DIR.mkdir(mode=0o700,parents=True,exist_ok=True)
 u=shutil.disk_usage(MODEL_DIR); files=[{'filename':p.name,'size_bytes':p.stat().st_size,'managed_copy':True} for p in sorted(MODEL_DIR.glob('*.gguf')) if p.is_file()]
 return {'free_bytes':u.free,'total_bytes':u.total,'models':files,'managed_directory':str(MODEL_DIR),'policy':'Arena odczytuje i usuwa wyłącznie własne pliki z managed-models.'}
