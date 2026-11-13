#!/usr/bin/env python3
import calendar, csv, hashlib, io, json, os, re, shutil, sqlite3, threading, time, urllib.request, urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import chess, chess.pgn
import league
import model_store
import engine_runner
import passkey_test

ROOT = Path(__file__).resolve().parent
MODEL_DIR = model_store.MANAGED_DIR
DB = ROOT / 'arena.sqlite3'
LOG_DIR = ROOT / 'logs'
MODEL_RESPONSES_LOG = LOG_DIR / 'model-responses.jsonl'
LIVE_EVENTS_LOG = LOG_DIR / 'recent-events.jsonl'
MODEL_RESPONSE_RETENTION_SECONDS = 3 * 24 * 60 * 60
LIVE_EVENT_RETENTION_SECONDS = 7 * 24 * 60 * 60
LOG_MAX_BYTES = 1024 * 1024
LLM = 'http://127.0.0.1:8088/v1/chat/completions'
HOST, PORT = '127.0.0.1', 8090
lock = threading.RLock()
board = chess.Board()
moves, timings = [], []
illegal_moves = 0
running = False
started_at = time.time()
last_error = ''
last_move = None
tournament_thread = None
tournament_stop_event = threading.Event()
tournament_runtime = None
tournament_error = ''
live_status = {'stage':'idle','message':'Oczekiwanie na turniej','model':None,'color':None,'fen':None,'legal_moves':[],'response':None,'updated_at':time.time(),'generation_started_at':None,'moves':[],'events':[]}
live_lock = threading.RLock()

def _prune_jsonl(path, retention_seconds, max_entries):
    """Keep only recent, parseable records and atomically bound the diagnostic log."""
    try:
        lines = path.read_text(encoding='utf-8').splitlines()
    except FileNotFoundError:
        return
    cutoff = time.time() - retention_seconds
    kept = []
    for line in lines:
        try:
            record = json.loads(line)
            stamp = record.get('at')
            created = calendar.timegm(time.strptime(stamp, '%Y-%m-%dT%H:%M:%SZ')) if stamp else 0
            if created >= cutoff:
                kept.append(line)
        except (ValueError, TypeError, json.JSONDecodeError):
            continue
    kept = kept[-max_entries:]
    content = '\n'.join(kept) + ('\n' if kept else '')
    if len(content.encode('utf-8')) > LOG_MAX_BYTES:
        kept = kept[-max_entries // 4:]
        content = '\n'.join(kept) + ('\n' if kept else '')
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(content, encoding='utf-8')
    temporary.replace(path)


def _append_jsonl(path, record, max_entries, retention_seconds):
    """Append a diagnostic record and enforce age/count/size retention."""
    LOG_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(record, ensure_ascii=False, separators=(',', ':')) + '\n')
    _prune_jsonl(path, retention_seconds, max_entries)


def _read_jsonl(path, limit, retention_seconds):
    _prune_jsonl(path, retention_seconds, limit)
    try:
        lines = path.read_text(encoding='utf-8').splitlines()[-limit:]
    except OSError:
        return []
    records = []
    for line in lines:
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def set_live(stage, message, **fields):
    with live_lock:
        now = time.time()
        if stage == 'generating':
            fields.setdefault('generation_started_at', now)
        elif 'generation_started_at' not in fields:
            fields['generation_started_at'] = None
        live_status.update({'stage':stage,'message':message,'updated_at':now,**fields})
        event_fields = ('model','color','response','proposed_move','last_move','pairing_id','question_no','error','result','tournament_id','game_id','move_count','elapsed_seconds')
        event = {'at':time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(now)),
                 'stage':stage,'message':message,
                 **{k:v for k,v in fields.items() if k in event_fields}}
        live_status['events'] = ([event] + live_status.get('events', []))[:40]
        try:
            _append_jsonl(LIVE_EVENTS_LOG, event, 500, LIVE_EVENT_RETENTION_SECONDS)
            response = fields.get('response')
            if isinstance(response, str) and response.strip():
                _append_jsonl(MODEL_RESPONSES_LOG, {
                    'at': event['at'], 'stage': stage, 'model': fields.get('model'),
                    'color': fields.get('color'), 'pairing_id': fields.get('pairing_id'),
                    'response': response, 'proposed_move': fields.get('proposed_move'),
                    'elapsed_seconds': fields.get('elapsed_seconds'),
                }, 200, MODEL_RESPONSE_RETENTION_SECONDS)
        except OSError as exc:
            # Logging is best-effort and must never abort a running tournament.
            live_status['log_write_error'] = str(exc)


def get_live():
    with live_lock:
        snapshot = dict(live_status)
        if not snapshot.get('events'):
            snapshot['events'] = list(reversed(_read_jsonl(LIVE_EVENTS_LOG, 40, LIVE_EVENT_RETENTION_SECONDS)))
            responses = _read_jsonl(MODEL_RESPONSES_LOG, 1, MODEL_RESPONSE_RETENTION_SECONDS)
            if responses:
                snapshot['response'] = responses[-1].get('response')
        return snapshot


def _is_managed_filename(filename):
    try:
        path = (model_store.MANAGED_DIR / filename).resolve()
        return path.parent == model_store.MANAGED_DIR.resolve() and path.suffix.lower() == '.gguf'
    except (OSError, TypeError):
        return False


def models():
    # Only models with an explicitly verified license may enter the match selector.
    with league.connect() as c:
        rows = c.execute('''SELECT DISTINCT v.filename FROM model_versions v
            JOIN league_models m ON m.id=v.model_id
            WHERE m.active=1 AND v.status='downloaded' AND v.license_verified=1
            AND v.filename IS NOT NULL ORDER BY v.filename''').fetchall()
    return sorted(r['filename'] for r in rows
                  if (model_store.MANAGED_DIR / r['filename']).is_file()
                  and _is_managed_filename(r['filename']))

def active_model():
    found = models()
    return found[0] if found else None


def db_init():
    league.init()
    with sqlite3.connect(DB) as c:
        c.execute('''CREATE TABLE IF NOT EXISTS games (
            id INTEGER PRIMARY KEY, started_at TEXT NOT NULL, ended_at TEXT NOT NULL,
            white_model TEXT NOT NULL, black_model TEXT NOT NULL, result TEXT NOT NULL,
            pgn TEXT NOT NULL, move_count INTEGER NOT NULL, illegal_attempts INTEGER NOT NULL,
            avg_move_seconds REAL NOT NULL)''')
        cols = {r[1] for r in c.execute('PRAGMA table_info(games)')}
        c.execute('''CREATE TABLE IF NOT EXISTS passkey_test_answers (
            id INTEGER PRIMARY KEY, tournament_id INTEGER NOT NULL, model_id INTEGER NOT NULL,
            model_name TEXT NOT NULL, question_no INTEGER NOT NULL, question TEXT NOT NULL,
            status TEXT NOT NULL, answer TEXT NOT NULL DEFAULT '', elapsed_seconds REAL,
            created_at TEXT NOT NULL, UNIQUE(tournament_id,model_id,question_no))''')
        passkey_cols={r[1] for r in c.execute('PRAGMA table_info(passkey_test_answers)')}
        if 'question_fingerprint' not in passkey_cols:
            c.execute('ALTER TABLE passkey_test_answers ADD COLUMN question_fingerprint TEXT')
        # Backfill historical answers by their stored exact question text.
        for answer_id, question in c.execute('SELECT id,question FROM passkey_test_answers WHERE question_fingerprint IS NULL').fetchall():
            c.execute('UPDATE passkey_test_answers SET question_fingerprint=? WHERE id=?',
                      (passkey_test.question_fingerprint(question),answer_id))
        for name, declaration in (('tournament_id', 'INTEGER'), ('pairing_id', 'INTEGER'),
                                  ('move_times_json', "TEXT NOT NULL DEFAULT '[]'"),
                                  ('inference_metadata_json', "TEXT NOT NULL DEFAULT '{}'")):
            if name not in cols:
                c.execute(f'ALTER TABLE games ADD COLUMN {name} {declaration}')


def llm_health():
    try:
        req = urllib.request.Request('http://127.0.0.1:8088/health')
        with urllib.request.urlopen(req, timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def ask_model(model, b):
    legal = [m.uci() for m in b.legal_moves]
    prompt = (
        'You are playing chess. Select exactly one move from the LEGAL MOVES list. '
        'Return only the UCI move, with no explanation.\n'
        f'FEN: {b.fen()}\nLEGAL MOVES: {", ".join(legal)}\nMOVE:'
    )
    payload = json.dumps({
        'messages': [{'role': 'user', 'content': prompt}],
        'temperature': 0, 'max_tokens': 24, 'stream': False,
        'chat_template_kwargs': {'enable_thinking': False}
    }).encode()
    req = urllib.request.Request(LLM, data=payload, headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=90) as r:
        data = json.loads(r.read().decode())
    content = data['choices'][0]['message'].get('content', '')
    match = re.search(r'(?<![a-h1-8])([a-h][1-8][a-h][1-8][qrbn]?)(?![a-h1-8])', content.lower())
    if not match:
        raise ValueError('Model nie zwrócił ruchu UCI: ' + repr(content[:120]))
    move = chess.Move.from_uci(match.group(1))
    if move not in b.legal_moves:
        raise ValueError('Model zwrócił nielegalny ruch: ' + move.uci())
    return move


def finish_game():
    global running
    outcome = board.outcome(claim_draw=True)
    if not outcome:
        return
    running = False
    game = chess.pgn.Game.from_board(board)
    game.headers['Event'] = 'Local AI Chess Arena prototype'
    game.headers['White'] = active_model() or 'unknown'
    game.headers['Black'] = active_model() or 'unknown'
    game.headers['Result'] = outcome.result()
    with sqlite3.connect(DB) as c:
        c.execute('INSERT INTO games(started_at,ended_at,white_model,black_model,result,pgn,move_count,illegal_attempts,avg_move_seconds) VALUES(?,?,?,?,?,?,?,?,?)',
                  (time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(started_at)),
                   time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                   active_model() or 'unknown', active_model() or 'unknown', outcome.result(),
                   str(game), len(moves), illegal_moves,
                   sum(timings)/len(timings) if timings else 0.0))



def _version_path(version):
    if not version or version['license_verified'] != 1 or version['status'] != 'downloaded' or not version['filename']:
        raise RuntimeError('Wersja modelu nie ma zatwierdzonej licencji lub pobranego pliku')
    if version['managed_copy'] != 1:
        raise RuntimeError('Arena odrzuca pliki spoza własnego katalogu managed-models')
    base = model_store.MANAGED_DIR
    path = (base / version['filename']).resolve()
    if path.parent != base.resolve() or not path.is_file() or path.suffix.lower() != '.gguf':
        raise RuntimeError(f'Brak bezpiecznego pliku GGUF dla wersji {version["id"]}')
    expected_size = version.get('size_bytes')
    expected_sha256 = version.get('sha256')
    if not isinstance(expected_size, int) or expected_size <= 0 or not expected_sha256:
        raise RuntimeError(f'Brak kompletnego manifestu integralności dla wersji {version["id"]}')
    digest = hashlib.sha256()
    actual_size = 0
    with path.open('rb') as model_file:
        for chunk in iter(lambda: model_file.read(1024 * 1024), b''):
            actual_size += len(chunk)
            digest.update(chunk)
    if actual_size != expected_size or digest.hexdigest().lower() != str(expected_sha256).lower():
        raise RuntimeError(f'Plik GGUF wersji {version["id"]} nie zgadza się z manifestem SHA-256/rozmiaru; model nie zostanie uruchomiony')
    return path


def _version_record(version_id):
    with league.connect() as c:
        row = c.execute("SELECT v.*,m.name,m.active FROM model_versions v JOIN league_models m ON m.id=v.model_id WHERE v.id=?", (version_id,)).fetchone()
    if row is None or not row['active']:
        raise RuntimeError(f'Nieaktywna lub nieznana wersja modelu: {version_id}')
    if row['license_verified'] != 1 or not row['filename'] or not row['source'] or not row['sha256'] or not row['size_bytes']:
        raise RuntimeError(f'Wersja {version_id} nie ma kompletnego, zweryfikowanego manifestu pobrania')
    if row['draft_filename'] and (not row['draft_sha256'] or not row['draft_size_bytes'] or not row['draft_download_url'] or not row['draft_source_path']):
        raise RuntimeError(f'Wersja {version_id} ma niekompletny manifest modelu draft')
    return dict(row)


def _ensure_version_file(version_id):
    version = _version_record(version_id)
    expected = model_store.MANAGED_DIR / version['filename']
    if expected.exists():
        if version['managed_copy'] != 1 or expected.resolve().parent != model_store.MANAGED_DIR.resolve():
            raise RuntimeError('Znaleziony plik nie jest oznaczoną kopią zarządzaną przez Arenę')
        if expected.stat().st_size != version['size_bytes']:
            raise RuntimeError(f'Rozmiar istniejącego modelu {version_id} nie zgadza się z manifestem')
        return str(_version_path(version))
    import urllib.parse
    metadata = json.loads(version.get('metadata_json') or '{}')
    download_url = metadata.get('download_url') or (
        version['source'].rstrip('/') + '/resolve/main/' + '/'.join(urllib.parse.quote(part, safe='') for part in (metadata.get('source_path') or version['filename']).split('/')))
    model_store.download_version(version_id, download_url, version['filename'],
                                 version['sha256'], int(version['size_bytes']), True)
    with league.connect() as c:
        refreshed = c.execute("SELECT id,filename,status,license_verified,managed_copy,sha256,size_bytes FROM model_versions WHERE id=?", (version_id,)).fetchone()
    return str(_version_path(dict(refreshed)))


def _ensure_version_assets(version_id):
    """Return target/draft paths, fetching and verifying both assets on demand."""
    target_path = _ensure_version_file(version_id)
    version = _version_record(version_id)
    draft_path = None
    if version.get('draft_filename'):
        expected = model_store.MANAGED_DIR / version['draft_filename']
        if expected.exists():
            if version['draft_managed_copy'] != 1 or expected.resolve().parent != model_store.MANAGED_DIR.resolve():
                raise RuntimeError('Znaleziony plik draft nie jest kopią zarządzaną przez Arenę')
            digest = hashlib.sha256()
            actual_size = 0
            with expected.open('rb') as draft_file:
                for chunk in iter(lambda: draft_file.read(1024 * 1024), b''):
                    actual_size += len(chunk); digest.update(chunk)
            if actual_size != version['draft_size_bytes'] or digest.hexdigest().lower() != version['draft_sha256'].lower():
                raise RuntimeError('Plik draft nie zgadza się z manifestem SHA-256/rozmiaru')
        else:
            model_store.download_draft_version(version_id, version['draft_download_url'],
                version['draft_filename'], version['draft_sha256'], int(version['draft_size_bytes']), True)
        draft_path = str(expected)
    return {'path': target_path, 'draft_path': draft_path}


def cleanup_unneeded_models(keep_version_ids=()):
    keep = {int(value) for value in keep_version_ids}
    with league.connect() as c:
        rows = c.execute("SELECT id FROM model_versions WHERE (managed_copy=1 AND filename IS NOT NULL) OR (draft_managed_copy=1 AND draft_filename IS NOT NULL) ORDER BY id").fetchall()
    removed = []
    for row in rows:
        if row['id'] in keep:
            continue
        try:
            removed.append(model_store.cleanup_version(row['id']))
        except (PermissionError, RuntimeError, FileNotFoundError):
            continue
    return removed


def _prepare_single_model(player):
    version_id = player['version_id']
    cleanup_unneeded_models({version_id})
    version = _version_record(version_id)
    expected = model_store.MANAGED_DIR / version['filename']
    required_bytes = 0 if expected.is_file() else int(version['size_bytes'])
    if version['draft_filename'] and not (model_store.MANAGED_DIR / version['draft_filename']).is_file():
        required_bytes += int(version['draft_size_bytes'])
    usage = shutil.disk_usage(model_store.MANAGED_DIR)
    if usage.free < required_bytes + model_store.MIN_FREE_MARGIN:
        raise OSError('Za mało miejsca na model i jego draft do testu tej partii; turniej pozostaje możliwy do wznowienia')
    try:
        return _ensure_version_assets(version_id)
    except Exception:
        cleanup_unneeded_models()
        raise


def _prepare_game_models(white, black):
    needed = {white['version_id'], black['version_id']}
    cleanup_unneeded_models(needed)
    versions = [_version_record(value) for value in (white['version_id'], black['version_id'])]
    required_bytes = sum((int(v['size_bytes']) if not (model_store.MANAGED_DIR / v['filename']).is_file() else 0) +
                         (int(v['draft_size_bytes'] or 0) if v['draft_filename'] and not (model_store.MANAGED_DIR / v['draft_filename']).is_file() else 0)
                         for v in versions)
    usage = shutil.disk_usage(model_store.MANAGED_DIR)
    if usage.free < required_bytes + model_store.MIN_FREE_MARGIN:
        raise OSError('Za mało miejsca na modele potrzebne w tej partii; turniej pozostaje możliwy do wznowienia')
    prepared = []
    try:
        for player in (white, black):
            prepared.append(_ensure_version_assets(player['version_id']))
    except Exception:
        cleanup_unneeded_models()
        raise
    return prepared


def live_moves_snapshot(move):
    with live_lock:
        previous = list(live_status.get('moves') or [])
    previous.append(dict(move))
    return previous[-200:]


def tournament_loop(tournament_id):
    global tournament_runtime, tournament_error
    # Hold the storage lifecycle lock for the full tournament: otherwise the
    # manual cleanup endpoint can unlink a model after preparation but before
    # a later turn reloads it. Downloads and cleanup use the same re-entrant lock.
    model_store.MODEL_LIFECYCLE_LOCK.acquire()
    try:
        with league.connect() as c:
            tournament = c.execute('SELECT * FROM tournaments WHERE id=?', (tournament_id,)).fetchone()
            if tournament is None:
                raise RuntimeError('Nie znaleziono turnieju')
            draft_enabled = tournament['rules_version'] == '2'
            entries = c.execute("SELECT e.model_id,e.version_id,m.name FROM tournament_entries e JOIN league_models m ON m.id=e.model_id WHERE e.tournament_id=?", (tournament_id,)).fetchall()
            players = {r['model_id']: {'name': r['name'], 'version_id': r['version_id']} for r in entries}
            pairings = c.execute("SELECT id,white_model_id,black_model_id FROM tournament_pairings WHERE tournament_id=? AND status='pending' AND stage='league' ORDER BY id", (tournament_id,)).fetchall()
            if any(row['white_model_id'] == row['black_model_id'] for row in pairings):
                raise RuntimeError('Nieprawidłowa para turniejowa: model nie może grać przeciwko samemu sobie')
            if not pairings and c.execute("SELECT COUNT(*) FROM tournament_entries WHERE tournament_id=?", (tournament_id,)).fetchone()[0] < 2:
                raise RuntimeError('Turniej nie ma wystarczającej liczby zawodników')
            c.execute("UPDATE tournaments SET status='running' WHERE id=?", (tournament_id,))
        set_live('starting','Przygotowanie turnieju', tournament_id=tournament_id)
        tournament_runtime = engine_runner.LlamafileRuntime(on_status=set_live)

        # Reuse completed answers for this competitor across tournaments. Once its
        # three knowledge answers are known, run at most one random health check per tournament.
        for player_id, player in players.items():
            with sqlite3.connect(DB) as db:
                known_rows = db.execute("SELECT DISTINCT question_fingerprint FROM passkey_test_answers WHERE model_id=? AND status='answered' AND question_no>0 AND question_fingerprint IS NOT NULL",
                                        (player_id,)).fetchall()
                random_done = db.execute("SELECT 1 FROM passkey_test_answers WHERE tournament_id=? AND model_id=? AND question_no=0 AND status='answered'",
                                          (tournament_id, player_id)).fetchone() is not None
            questions = passkey_test.questions_for_tournament([row[0] for row in known_rows], random_done)
            if not questions or tournament_stop_event.is_set():
                continue
            set_live('passkey_prepare',f'Przygotowanie testu Passkey: {player["name"]}', model=player['name'])
            assets = _prepare_single_model(player)
            player.update(assets)
            try:
                for question_no, question in questions:
                    label=f'kontrola losowa' if question_no == 0 else f'pytanie {question_no}/3'
                    set_live('passkey_question',f'Test Passkey: {label}', model=player['name'], question_no=question_no)
                    if tournament_stop_event.is_set():
                        break
                    answer = passkey_test.ask_passkey_question(tournament_runtime, player['path'], question, tournament_stop_event, draft_model_path=player.get('draft_path'))
                    with sqlite3.connect(DB) as db:
                        db.execute("INSERT OR REPLACE INTO passkey_test_answers (tournament_id,model_id,model_name,question_no,question,status,answer,elapsed_seconds,created_at,question_fingerprint) VALUES(?,?,?,?,?,?,?,?,?,?)",
                            (tournament_id, player_id, player['name'], question_no, question,
                             answer['status'], answer['answer'], answer['seconds'],
                             time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                             passkey_test.question_fingerprint(question)))
            finally:
                tournament_runtime.stop()
                cleanup_unneeded_models()
            if tournament_stop_event.is_set():
                break

        game_runner = engine_runner.SequentialChessRu