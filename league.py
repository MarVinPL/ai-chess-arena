import hashlib,json,re,shutil,sqlite3,time,math
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
  CREATE TABLE IF NOT EXISTS tournaments(id INTEGER PRIMARY KEY,name TEXT NOT NULL,format TEXT NOT NULL DEFAULT 'PURE_AI',status TEXT NOT NULL DEFAULT 'planned',created_at TEXT NOT NULL,rules_version TEXT NOT NULL DEFAULT '1',qualification_percent INTEGER NOT NULL DEFAULT 80);
  CREATE TABLE IF NOT EXISTS tournament_entries(tournament_id INTEGER NOT NULL REFERENCES tournaments(id),model_id INTEGER NOT NULL REFERENCES league_models(id),version_id INTEGER REFERENCES model_versions(id),PRIMARY KEY(tournament_id,model_id));
  CREATE TABLE IF NOT EXISTS tournament_pairings(id INTEGER PRIMARY KEY,tournament_id INTEGER NOT NULL REFERENCES tournaments(id),white_model_id INTEGER NOT NULL REFERENCES league_models(id),black_model_id INTEGER NOT NULL REFERENCES league_models(id),leg INTEGER NOT NULL CHECK(leg IN (1,2)),status TEXT NOT NULL DEFAULT 'pending',game_id INTEGER,UNIQUE(tournament_id,white_model_id,black_model_id,leg));
  CREATE INDEX IF NOT EXISTS idx_pairings_tournament ON tournament_pairings(tournament_id,status);
  CREATE TABLE IF NOT EXISTS tournament_seeds(
   tournament_id INTEGER NOT NULL REFERENCES tournaments(id),
   model_id INTEGER NOT NULL REFERENCES league_models(id),
   seed INTEGER NOT NULL,
   PRIMARY KEY(tournament_id,model_id),
   UNIQUE(tournament_id,seed)
  );
  CREATE TABLE IF NOT EXISTS token_budget_profiles(config_key TEXT PRIMARY KEY,current_max_tokens INTEGER NOT NULL,pending_max_tokens INTEGER,low_usage_samples INTEGER NOT NULL DEFAULT 0,last_reason TEXT,last_tournament_id INTEGER,updated_at TEXT NOT NULL);
  CREATE TABLE IF NOT EXISTS tournament_entry_budgets(tournament_id INTEGER NOT NULL REFERENCES tournaments(id),model_id INTEGER NOT NULL REFERENCES league_models(id),version_id INTEGER NOT NULL REFERENCES model_versions(id),config_key TEXT NOT NULL,base_max_tokens INTEGER NOT NULL,effective_max_tokens INTEGER NOT NULL,PRIMARY KEY(tournament_id,model_id));
  CREATE TABLE IF NOT EXISTS token_budget_observations(id INTEGER PRIMARY KEY,tournament_id INTEGER NOT NULL,model_id INTEGER NOT NULL,config_key TEXT NOT NULL,max_tokens INTEGER NOT NULL,completion_tokens INTEGER,finish_reason TEXT,truncated INTEGER NOT NULL,valid_move INTEGER NOT NULL,attempt INTEGER NOT NULL,observed_at TEXT NOT NULL);
  CREATE INDEX IF NOT EXISTS idx_token_budget_observations_config ON token_budget_observations(config_key,tournament_id);

  """)
  cols={r[1] for r in c.execute('PRAGMA table_info(model_versions)')}
  if 'license_verified' not in cols:
   c.execute('ALTER TABLE model_versions ADD COLUMN license_verified INTEGER NOT NULL DEFAULT 0')
  tournament_cols={r[1] for r in c.execute('PRAGMA table_info(tournaments)')}
  if 'qualification_percent' not in tournament_cols: c.execute('ALTER TABLE tournaments ADD COLUMN qualification_percent INTEGER NOT NULL DEFAULT 80')
  pair_cols={r[1] for r in c.execute('PRAGMA table_info(tournament_pairings)')}
  if 'stage' not in pair_cols: c.execute("ALTER TABLE tournament_pairings ADD COLUMN stage TEXT NOT NULL DEFAULT 'league'")
  if 'round_no' not in pair_cols: c.execute("ALTER TABLE tournament_pairings ADD COLUMN round_no INTEGER")
  if 'match_no' not in pair_cols: c.execute("ALTER TABLE tournament_pairings ADD COLUMN match_no INTEGER")
  if 'winner_model_id' not in pair_cols: c.execute("ALTER TABLE tournament_pairings ADD COLUMN winner_model_id INTEGER REFERENCES league_models(id)")
  for name, declaration in (('draft_filename','TEXT'),('draft_sha256','TEXT'),('draft_size_bytes','INTEGER'),('draft_status',"TEXT NOT NULL DEFAULT 'catalogued'"),('draft_managed_copy','INTEGER NOT NULL DEFAULT 0'),('draft_download_url','TEXT'),('draft_source_path','TEXT')):
   if name not in cols:
    c.execute(f'ALTER TABLE model_versions ADD COLUMN {name} {declaration}')
def normalize_inference_config(metadata):
 metadata = metadata or {}
 inference = metadata.get('inference', {})
 if inference is None: inference = {}
 if not isinstance(inference, dict): raise ValueError('Parametry inferencji muszą być obiektem')
 unknown = set(inference) - {'thinking', 'temperature', 'max_tokens'}
 if unknown: raise ValueError('Nieobsługiwane parametry inferencji: ' + ', '.join(sorted(unknown)))
 thinking = inference.get('thinking', False)
 if not isinstance(thinking, bool): raise ValueError('Parametr thinking musi mieć wartość true albo false')
 temperature = inference.get('temperature', 0.0)
 if isinstance(temperature, bool) or not isinstance(temperature, (int, float)) or not 0 <= temperature <= 2:
  raise ValueError('Parametr temperature musi być liczbą od 0 do 2')
 max_tokens = inference.get('max_tokens', 512 if thinking else 16)
 if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or not 1 <= max_tokens <= 8192:
  raise ValueError('Parametr max_tokens musi być liczbą całkowitą od 1 do 8192')
 return {'thinking': thinking, 'temperature': float(temperature), 'max_tokens': max_tokens}


def adaptive_budget_config_key(model_sha256, draft_sha256, metadata):
 inference = normalize_inference_config(metadata)
 payload = {'model_sha256': (model_sha256 or '').lower(),
            'draft_sha256': (draft_sha256 or '').lower(),
            'inference': inference}
 return hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def prepare_tournament_budgets(tournament_id):
 snapshots={}
 with connect() as c:
  entries=c.execute("SELECT e.model_id,e.version_id,v.sha256,v.draft_sha256,v.metadata_json FROM tournament_entries e JOIN model_versions v ON v.id=e.version_id WHERE e.tournament_id=?",(tournament_id,)).fetchall()
  for row in entries:
   metadata=json.loads(row['metadata_json'] or '{}')
   inference=normalize_inference_config(metadata)
   base=inference['max_tokens']
   key=adaptive_budget_config_key(row['sha256'],row['draft_sha256'],metadata)
   existing=c.execute('SELECT effective_max_tokens FROM tournament_entry_budgets WHERE tournament_id=? AND model_id=?',(tournament_id,row['model_id'])).fetchone()
   if existing:
    snapshots[row['model_id']]=int(existing['effective_max_tokens'])
    continue
   now=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())
   c.execute('INSERT OR IGNORE INTO token_budget_profiles(config_key,current_max_tokens,updated_at) VALUES(?,?,?)',(key,base,now))
   profile=c.execute('SELECT current_max_tokens,pending_max_tokens FROM token_budget_profiles WHERE config_key=?',(key,)).fetchone()
   effective=int(profile['pending_max_tokens'] if profile['pending_max_tokens'] is not None else profile['current_max_tokens'])
   if profile['pending_max_tokens'] is not None:
    c.execute('UPDATE token_budget_profiles SET current_max_tokens=?,pending_max_tokens=NULL,updated_at=? WHERE config_key=?',(effective,now,key))
   c.execute('INSERT INTO tournament_entry_budgets(tournament_id,model_id,version_id,config_key,base_max_tokens,effective_max_tokens) VALUES(?,?,?,?,?,?)',(tournament_id,row['model_id'],row['version_id'],key,base,effective))
   snapshots[row['model_id']]=effective
 return snapshots


def token_budget_info(model_sha256, draft_sha256, metadata):
 inference=normalize_inference_config(metadata)
 base=inference['max_tokens']
 key=adaptive_budget_config_key(model_sha256,draft_sha256,metadata)
 with connect() as c:
  row=c.execute('SELECT current_max_tokens,pending_max_tokens,last_reason,low_usage_samples FROM token_budget_profiles WHERE config_key=?',(key,)).fetchone()
 if row is None:
  return {'config_key':key,'current_max_tokens':base,'pending_max_tokens':None,'last_reason':None,'low_usage_samples':0}
 return {'config_key':key,'current_max_tokens':int(row['current_max_tokens']),'pending_max_tokens':int(row['pending_max_tokens']) if row['pending_max_tokens'] is not None else None,'last_reason':row['last_reason'],'low_usage_samples':int(row['low_usage_samples'])}


def record_token_budget_observations(tournament_id, model_id, model_sha256, draft_sha256, metadata, observations):
 if not observations:
  return None
 key=adaptive_budget_config_key(model_sha256,draft_sha256,metadata)
 now=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())
 with connect() as c:
  snapshot=c.execute('SELECT effective_max_tokens FROM tournament_entry_budgets WHERE tournament_id=? AND model_id=?',(tournament_id,model_id)).fetchone()
  if snapshot is None:
   return None
  current=int(snapshot['effective_max_tokens'])
  c.execute('INSERT OR IGNORE INTO token_budget_profiles(config_key,current_max_tokens,updated_at) VALUES(?,?,?)',(key,current,now))
  profile=c.execute('SELECT current_max_tokens,pending_max_tokens,low_usage_samples FROM token_budget_profiles WHERE config_key=?',(key,)).fetchone()
  pending=int(profile['pending_max_tokens']) if profile['pending_max_tokens'] is not None else None
  low_samples=int(profile['low_usage_samples'])
  has_truncation=False
  low_count=0
  clear_non_low=False
  for obs in observations:
   max_tokens=int(obs.get('max_tokens') or current)
   completion=obs.get('completion_tokens')
   finish=obs.get('finish_reason')
   truncated=bool(obs.get('truncated'))
   valid=bool(obs.get('valid_move'))
   attempt=int(obs.get('attempt') or 1)
   c.execute('INSERT INTO token_budget_observations(tournament_id,model_id,config_key,max_tokens,completion_tokens,finish_reason,truncated,valid_move,attempt,observed_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
    (tournament_id,model_id,key,max_tokens,int(completion) if isinstance(completion,int) and completion>=0 else None,str(finish)[:40] if finish is not None else None,int(truncated),int(valid),attempt,now))
   if truncated:
    has_truncation=True
   elif valid and isinstance(completion,int) and finish in ('stop','eos','end_turn'):
    if completion <= max(1,int(max_tokens*0.25)):
     low_count+=1
    else:
     clear_non_low=True
  reason=None
  if has_truncation:
   pending=min(8192,max(current*2,pending or current))
   low_samples=0
   reason='Odpowiedź osiągnęła limit tokenów lub finish_reason=length'
  elif low_count:
   low_samples=0 if clear_non_low else low_samples+low_count
   if low_samples>=20:
    next_budget=max(8,int(current*0.75))
    if next_budget<current and not (pending is not None and pending>current):
     pending=next_budget
     reason=f'{low_samples} poprawnych odpowiedzi zużyło nie więcej niż 25% budżetu'
    low_samples=0
  elif clear_non_low:
   low_samples=0
   if pending is not None and pending<current:
    pending=None
    reason='Wykorzystanie budżetu nie potwierdza bezpiecznego zmniejszenia'
  if reason:
   c.execute('UPDATE token_budget_profiles SET pending_max_tokens=?,low_usage_samples=?,last_reason=?,last_tournament_id=?,updated_at=? WHERE config_key=?',(pending,low_samples,reason,tournament_id,now,key))
  else:
   c.execute('UPDATE token_budget_profiles SET low_usage_samples=?,updated_at=? WHERE config_key=?',(low_samples,now,key))
 return {'config_key':key,'current_max_tokens':current,'pending_max_tokens':pending,'reason':reason,'low_usage_samples':low_samples}

def register_model(name,filename=None,source='',license='',metadata=None,format='PURE_AI',license_verified=False):
 if not name or format not in ('PURE_AI','AI_ENGINE'): raise ValueError('Nieprawidłowa nazwa lub format')
 if license_verified:
  normalized_license=(license or '').strip().lower()
  if not (source or '').strip().lower().startswith('https://'):
   raise ValueError('Zatwierdzona licencja wymaga źródła HTTPS')
  if not normalized_license or normalized_license in ('nieustalona','unknown','n/a','unverified','unspecified','none'):
   raise ValueError('Nie można zatwierdzić nieustalonej licencji')
 metadata = dict(metadata or {})
 inference_config = normalize_inference_config(metadata)
 metadata['inference'] = inference_config
 inference_key = json.dumps(inference_config, sort_keys=True, separators=(',', ':'))
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
 asset_key = hashlib.sha256(f"{digest or ''}:{draft_sha if draft else ''}".encode()).hexdigest()[:16]
 config_key = hashlib.sha256(f"{digest or ''}:{draft_sha if draft else ''}:{inference_key}".encode()).hexdigest()[:16]
 if filename:
  with connect() as c:
   collision=c.execute('SELECT 1 FROM model_versions WHERE filename IN (?,?) OR draft_filename IN (?,?) LIMIT 1',(filename,filename,filename,filename)).fetchone()
  if collision:
   metadata.setdefault('source_path', metadata.get('filename') or filename)
   filename=f'{Path(filename).stem}-{asset_key}.gguf'
 if draft:
  with connect() as c:
   collision=c.execute('SELECT 1 FROM model_versions WHERE filename=? OR draft_filename=? LIMIT 1',(draft_filename,draft_filename)).fetchone()
  if collision:
   draft_filename=f'{Path(draft_filename).stem}-{asset_key}.gguf'
 now=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())
 with connect() as c:
  c.execute('INSERT OR IGNORE INTO league_models(name,format,created_at) VALUES(?,?,?)',(name,format,now)); m=c.execute('SELECT id,format FROM league_models WHERE name=?',(name,)).fetchone()
  if m['format']!=format: raise ValueError('Model istnieje w innym formacie')
  version_key=config_key if digest else (filename or ('catalog:'+name))
  c.execute('''INSERT OR IGNORE INTO model_versions(model_id,version_key,filename,sha256,size_bytes,source,license,metadata_json,status,license_verified,created_at,draft_filename,draft_sha256,draft_size_bytes,draft_download_url,draft_source_path) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(m['id'],version_key,filename,digest,size,source,license,json.dumps(metadata,ensure_ascii=False),status,int(bool(license_verified and source and license)),now,draft_filename if draft else None,draft.get('sha256') if draft else None,draft.get('size_bytes') if draft else None,draft.get('download_url') if draft else None,draft.get('source_path') if draft else None))
  vs=c.execute('SELECT * FROM model_versions WHERE model_id=? ORDER BY id DESC',(m['id'],)).fetchall()
  return {'id':m['id'],'name':name,'format':format,'versions':[dict(v) for v in vs]}
def create_round_robin(name,format='PURE_AI',qualification_percent=80):
 if isinstance(qualification_percent,bool) or not isinstance(qualification_percent,int) or not 75<=qualification_percent<=90:
  raise ValueError('Procent awansu do draftu musi wynosić od 75 do 90')
 with connect() as c:
  candidates=c.execute('''SELECT m.id,v.id AS version_id,v.sha256 AS model_sha256,
      COALESCE(v.draft_sha256,'') AS draft_sha256,v.metadata_json
      FROM league_models m JOIN model_versions v ON v.id=(
       SELECT v2.id FROM model_versions v2 WHERE v2.model_id=m.id AND v2.license_verified=1
       AND v2.source<>'' AND v2.filename IS NOT NULL AND v2.sha256 IS NOT NULL AND v2.size_bytes>0
       ORDER BY v2.id DESC LIMIT 1)
      WHERE m.active=1 AND m.format=? ORDER BY m.id''',(format,)).fetchall()
  # A competitor is an immutable model+draft+inference configuration. The same
  # GGUF may therefore enter twice when thinking differs, but exact duplicates do not.
  ms=[]; seen_configs=set()
  for candidate in candidates:
   metadata=json.loads(candidate['metadata_json'] or '{}')
   inference=normalize_inference_config(metadata)
   identity=((candidate['model_sha256'] or '').lower(),
             (candidate['draft_sha256'] or '').lower(),
             json.dumps(inference,sort_keys=True,separators=(',',':')))
   if identity in seen_configs: continue
   seen_configs.add(identity); ms.append(candidate)
  if len(ms)<2: raise ValueError('Potrzeba co najmniej dwóch różnych modeli z jawnie zweryfikowaną licencją')
  now=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())
  tid=c.execute("INSERT INTO tournaments(name,format,created_at,rules_version,qualification_percent) VALUES(?,?,?,'2',?)",(name,format,now,qualification_percent)).lastrowid
  for m in ms:
   c.execute('INSERT INTO tournament_entries(tournament_id,model_id,version_id) VALUES(?,?,?)',(tid,m['id'],m['version_id']))
  for i,a in enumerate(ms):
   for b in ms[i+1:]:
    c.execute("INSERT INTO tournament_pairings(tournament_id,white_model_id,black_model_id,leg,stage) VALUES(?,?,?,1,'league')",(tid,a['id'],b['id']))
    c.execute("INSERT INTO tournament_pairings(tournament_id,white_model_id,black_model_id,leg,stage) VALUES(?,?,?,2,'league')",(tid,b['id'],a['id']))
  expected_qualifiers=max(2,math.ceil(len(ms)*qualification_percent/100))
  expected_qualifiers=min(len(ms),expected_qualifiers)
  return {'id':tid,'name':name,'format':format,'players':len(ms),'qualification_percent':qualification_percent,'expected_qualifiers':expected_qualifiers,'games_planned':len(ms)*(len(ms)-1),'draft_games_planned':expected_qualifiers-1,'total_games_planned':len(ms)*(len(ms)-1)+expected_qualifiers-1,'draft_format':'ALL_WINNERS_PLUS_TOP_SCORING_REMAINDER','status':'planned'}

def _seed_order(size):
 order=[1,2]
 while len(order)<size:
  last=len(order)*2+1
  order=[x for seed in order for x in (seed,last-seed)]
 return order

def _league_standings(c,tournament_id):
 rows=c.execute('''SELECT e.model_id,m.name,
   COALESCE(SUM(CASE
    WHEN (g.result='1-0' AND p.white_model_id=e.model_id) OR (g.result='0-1' AND p.black_model_id=e.model_id) THEN 3
    WHEN g.result='1/2-1/2' THEN 1 ELSE 0 END),0) AS points,
   COALESCE(SUM(CASE WHEN (g.result='1-0' AND p.white_model_id=e.model_id) OR (g.result='0-1' AND p.black_model_id=e.model_id) THEN 1 ELSE 0 END),0) AS wins,
   COALESCE(SUM(CASE WHEN g.result='1/2-1/2' THEN 1 ELSE 0 END),0) AS draws
   FROM tournament_entries e JOIN league_models m ON m.id=e.model_id
   LEFT JOIN tournament_pairings p ON p.tournament_id=e.tournament_id AND p.stage='league'
    AND (p.white_model_id=e.model_id OR p.black_model_id=e.model_id)
   LEFT JOIN games g ON g.id=p.game_id
   WHERE e.tournament_id=? GROUP BY e.model_id,m.name''',(tournament_id,)).fetchall()
 return sorted((dict(r) for r in rows),key=lambda r:(-r['points'],-r['wins'],-r['draws'],r['name'].casefold()))

def _draft_qualifiers(standings,qualification_percent):
 target=max(2,math.ceil(len(standings)*qualification_percent/100))
 winners=[row for row in standings if row['wins']>0]
 non_winners=[row for row in standings if row['wins']==0]
 selected_ids={row['model_id'] for row in winners}
 for row in non_winners:
  if len(selected_ids)>=target: break
  selected_ids.add(row['model_id'])
 # Keep league standings order for seeding; every player with a win is retained
 # even when winners alone exceed the nominal percentage target.
 return [row for row in standings if row['model_id'] in selected_ids]

def _insert_draft_matches(c,tournament_id,round_no,pairs):
 for match_no,(white,black) in enumerate(pairs,1):
  existing=c.execute("SELECT leg FROM tournament_pairings WHERE tournament_id=? AND white_model_id=? AND black_model_id=?",(tournament_id,white,black)).fetchone()
  # The legacy uniqueness key includes leg; use the other leg slot for a draft rematch.
  leg=3-existing['leg'] if existing else 1
  c.execute("INSERT INTO tournament_pairings(tournament_id,white_model_id,black_model_id,leg,status,stage,round_no,match_no) VALUES(?,?,?,?, 'pending','draft',?,?)",
   (tournament_id,white,black,leg,round_no,match_no))

def next_draft_match(tournament_id):
 # Create each knockout round only after every match in the preceding round is complete.
 with connect() as c:
  entries=c.execute('SELECT COUNT(*) FROM tournament_entries WHERE tournament_id=?',(tournament_id,)).fetchone()[0]
  if entries<2: raise ValueError('Drabinka wymaga co najmniej dwóch zawodników')
  seeds=c.execute('SELECT model_id,seed FROM tournament_seeds WHERE tournament_id=? ORDER BY seed',(tournament_id,)).fetchall()
  if not seeds:
   total=c.execute("SELECT COUNT(*) FROM tournament_pairings WHERE tournament_id=? AND stage='league' AND status='completed'",(tournament_id,)).fetchone()[0]
   if total != entries*(entries-1): return None
   tournament=c.execute('SELECT qualification_percent FROM tournaments WHERE id=?',(tournament_id,)).fetchone()
   standings=_league_standings(c,tournament_id)
   qualified=_draft_qualifiers(standings,tournament['qualification_percent'] if tournament else 80)
   for idx,row in enumerate(qualified,1):
    c.execute('INSERT INTO tournament_seeds(tournament_id,model_id,seed) VALUES(?,?,?)',(tournament_id,row['model_id'],idx))
   seeds=c.execute('SELECT model_id,seed FROM tournament_seeds WHERE tournament_id=? ORDER BY seed',(tournament_id,)).fetchall()
  seed_map={r['seed']:r['model_id'] for r in seeds}
  last=c.execute("SELECT MAX(round_no) FROM tournament_pairings WHERE tournament_id=? AND stage='draft'",(tournament_id,)).fetchone()[0]
  if last is None:
   n=len(seeds); size=1
   while size<n: size*=2
   playin=2*n-size
   if playin:
    lower=list(range(n-playin+1,n+1))
    pairs=[(seed_map[lower[i]],seed_map[lower[-1-i]]) for i in range(len(lower)//2)]
    _insert_draft_matches(c,tournament_id,0,pairs); last=0
   else:
    order=_seed_order(size)
    pairs=[(seed_map[order[i]],seed_map[order[i+1]]) for i in range(0,size,2)]
    _insert_draft_matches(c,tournament_id,1,pairs); last=1
  rows=c.execute("SELECT * FROM tournament_pairings WHERE tournament_id=? AND stage='draft' AND round_no=? ORDER BY match_no",(tournament_id,last)).fetchall()
  pending=next((dict(r) for r in rows if r['status']=='pending'),None)
  if pending: return pending
  winners=[r['winner_model_id'] for r in rows]
  if not rows or any(w is None for w in winners): return None
  # A single play-in winner still advances when the rest received byes.
  if len(winners)==1 and last != 0: return None
  next_no=last+1
  existing=c.execute("SELECT 1 FROM tournament_pairings WHERE tournament_id=? AND stage='draft' AND round_no=? LIMIT 1",(tournament_id,next_no)).fetchone()
  if not existing:
   if last==0:
    n=len(seeds); size=1
    while size<n: size*=2
    byes=n-(2*n-size)
    reduced={i:seed_map[i] for i in range(1,byes+1)}
    for i,winner in enumerate(winners,byes+1): reduced[i]=winner
    bracket_size=1
    while bracket_size<len(reduced): bracket_size*=2
    order=_seed_order(bracket_size)
    pairs=[(reduced[order[i]],reduced[order[i+1]]) for i in range(0,bracket_size,2)]
   else:
    pairs=[(winners[i],winners[i+1]) for i in range(0,len(winners),2)]
   _insert_draft_matches(c,tournament_id,next_no,pairs)
  rows=c.execute("SELECT * FROM tournament_pairings WHERE tournament_id=? AND stage='draft' AND round_no=? ORDER BY match_no",(tournament_id,next_no)).fetchall()
  return next((dict(r) for r in rows if r['status']=='pending'),None)

def record_draft_result(pairing_id,result):
 with connect() as c:
  p=c.execute('''SELECT p.*,a.seed AS white_seed,b.seed AS black_seed
   FROM tournament_pairings p JOIN tournament_seeds a ON a.tournament_id=p.tournament_id AND a.model_id=p.white_model_id
   JOIN tournament_seeds b ON b.tournament_id=p.tournament_id AND b.model_id=p.black_model_id
   WHERE p.id=? AND p.stage='draft' ''',(pairing_id,)).fetchone()
  if not p: raise ValueError('Nie znaleziono meczu draft')
  if result=='1-0': winner=p['white_model_id']
  elif result=='0-1': winner=p['black_model_id']
  else:
   # A drawn knockout game is decided in favor of the higher seed (lower seed number).
   winner=p['white_model_id'] if p['white_seed']<p['black_seed'] else p['black_model_id']
  c.execute("UPDATE tournament_pairings SET status='completed',winner_model_id=? WHERE id=?",(winner,pairing_id))
  return winner

def draft_champion(tournament_id):
 with connect() as c:
  last=c.execute("SELECT MAX(round_no) FROM tournament_pairings WHERE tournament_id=? AND stage='draft'",(tournament_id,)).fetchone()[0]
  if last is None: return None
  rows=c.execute("SELECT winner_model_id,status FROM tournament_pairings WHERE tournament_id=? AND stage='draft' AND round_no=? ORDER BY match_no",(tournament_id,last)).fetchall()
  if len(rows)==1 and rows[0]['status']=='completed': return rows[0]['winner_model_id']
  return None

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
