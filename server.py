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
        event_fields = ('white','black','model','color','response','proposed_move','last_move','pairing_id','question_no','error','result','tournament_id','game_id','move_count','elapsed_seconds')
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
    """Fetch a missing GGUF on demand, then verify it against the pinned manifest."""
    version = _version_record(version_id)
    expected = model_store.MANAGED_DIR / version['filename']
    if not expected.is_file():
        try:
            metadata = json.loads(version.get('metadata_json') or '{}')
        except (TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f'Nie można odczytać manifestu wersji {version_id}') from exc
        url = metadata.get('download_url')
        revision = metadata.get('revision')
        source_path = metadata.get('source_path') or version['filename']
        if not url and not str(version['source']).startswith('https://huggingface.co/') and str(version['source']).lower().endswith('.gguf'):
            # Non-HF direct GGUF URLs remain supported with the same pinned size/hash checks.
            url = version['source']
        if not isinstance(url, str) or not url.startswith('https://'):
            raise RuntimeError(f'Wersja {version_id} nie ma bezpiecznego URL HTTPS')
        if str(version['source']).startswith('https://huggingface.co/'):
            if (not isinstance(revision, str) or not re.fullmatch(r'[0-9a-fA-F]{40}', revision) or
                    f'/resolve/{revision}/' not in url or not url.endswith('/' + source_path)):
                raise RuntimeError(f'Wersja {version_id} nie ma URL przypiętego do pełnego commita Hugging Face')
        elif url != version['source']:
            raise RuntimeError(f'URL pobrania wersji {version_id} nie zgadza się ze źródłem rejestru')
        model_store.download_version(
            version_id, url, version['filename'], version['sha256'],
            version['size_bytes'], True
        )
        version = _version_record(version_id)
    if version['managed_copy'] != 1 or expected.resolve().parent != model_store.MANAGED_DIR.resolve():
        raise RuntimeError('Plik GGUF nie jest oznaczoną kopią zarządzaną przez Arenę')
    return str(_version_path(version))


def _ensure_version_assets(version_id):
    """Fetch missing target/draft files only when a game actually needs them."""
    target_path = _ensure_version_file(version_id)
    version = _version_record(version_id)
    draft_path = None
    if version.get('draft_filename'):
        expected = model_store.MANAGED_DIR / version['draft_filename']
        if not expected.is_file():
            model_store.download_draft_version(
                version_id, version['draft_download_url'], version['draft_filename'],
                version['draft_sha256'], version['draft_size_bytes'], True
            )
            version = _version_record(version_id)
        if version['draft_managed_copy'] != 1 or expected.resolve().parent != model_store.MANAGED_DIR.resolve():
            raise RuntimeError('Plik draft nie jest oznaczoną kopią zarządzaną przez Arenę')
        digest = hashlib.sha256()
        actual_size = 0
        with expected.open('rb') as draft_file:
            for chunk in iter(lambda: draft_file.read(1024 * 1024), b''):
                actual_size += len(chunk); digest.update(chunk)
        if actual_size != version['draft_size_bytes'] or digest.hexdigest().lower() != version['draft_sha256'].lower():
            raise RuntimeError('Plik draft nie zgadza się z manifestem SHA-256/rozmiaru')
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
    model_store.MANAGED_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    version_id = player['version_id']
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
        raise


def _prepare_game_models(white, black):
    model_store.MANAGED_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    needed = {white['version_id'], black['version_id']}
    versions = [_version_record(value) for value in (white['version_id'], black['version_id'])]
    required_bytes = sum((int(v['size_bytes']) if not (model_store.MANAGED_DIR / v['filename']).is_file() else 0) +
                         (int(v['draft_size_bytes'] or 0) if v['draft_filename'] and not (model_store.MANAGED_DIR / v['draft_filename']).is_file() else 0)
                         for v in versions)
    usage = shutil.disk_usage(model_store.MANAGED_DIR)
    if usage.free < required_bytes + model_store.MIN_FREE_MARGIN:
        raise OSError('Za mało miejsca na modele potrzebne w tej partii; turniej pozostaje możliwy do wznowienia')
    prepared = []
    for player in (white, black):
        prepared.append(_ensure_version_assets(player['version_id']))
    return prepared


def live_moves_snapshot(move):
    with live_lock:
        previous = list(live_status.get('moves') or [])
    previous.append(dict(move))
    return previous[-200:]


def _registry_display_name_for_runtime_model(model_label, names_by_filename):
    """Translate runtime file labels to the canonical registry display name."""
    if not model_label:
        return None
    return names_by_filename.get(Path(str(model_label)).name)


def _registry_runtime_message(message, names_by_filename):
    """Replace any runtime filename embedded in status text with its registry name."""
    for filename, display_name in names_by_filename.items():
        message = message.replace(filename, display_name)
    return message


def _resolve_pairing_players(players, pairing):
    """Resolve the exact registry entrants assigned to the white/black sides."""
    white_id = pairing.get('white_model_id')
    black_id = pairing.get('black_model_id')
    if white_id == black_id:
        raise RuntimeError('Nieprawidłowa para turniejowa: model nie może grać przeciwko samemu sobie')
    white = players.get(white_id)
    black = players.get(black_id)
    if not white or not black:
        raise RuntimeError('Brak wersji zawodnika przypisanej do pary turniejowej')
    return white, black


class TournamentPreflightError(RuntimeError):
    """Tournament cannot start until every entrant is independently ready."""


def tournament_preflight(tournament_id):
    """Check metadata, license decision, and runtime; local weights are lazy-cached."""
    entries = league.tournament_entry_status(tournament_id)
    runtime_available = llm_health()
    blocked = []
    for entry in entries:
        reasons = list(entry['blockers'])
        if not runtime_available:
            reasons.append('lokalny runtime Llamafile jest niedostępny')
        entry['runtime_available'] = runtime_available
        entry['download_on_demand'] = bool(entry.get('manifest_ready') and entry.get('license_verified'))
        entry['blockers'] = reasons
        entry['ready_to_start'] = not reasons
        if reasons:
            blocked.append({'model_id': entry['model_id'], 'name': entry['name'],
                            'reasons': reasons})
    return {'tournament_id': tournament_id, 'ready': not blocked,
            'runtime_available': runtime_available, 'entrants': entries,
            'blocked': blocked}


def _preflight_error_message(preflight):
    details = []
    for item in preflight['blocked']:
        details.append(item['name'] + ': ' + ', '.join(item['reasons']))
    return 'Preflight zablokował start turnieju. ' + '; '.join(details)


def tournament_loop(tournament_id):
    global tournament_runtime, tournament_error
    # Hold the storage lifecycle lock for the full tournament: otherwise the
    # manual cleanup endpoint can unlink a model after preparation but before
    # a later turn reloads it. Downloads and cleanup use the same re-entrant lock.
    model_store.MODEL_LIFECYCLE_LOCK.acquire()
    try:
        preflight = tournament_preflight(tournament_id)
        if not preflight['ready']:
            raise TournamentPreflightError(_preflight_error_message(preflight))
        with league.connect() as c:
            tournament = c.execute('SELECT * FROM tournaments WHERE id=?', (tournament_id,)).fetchone()
            if tournament is None:
                raise RuntimeError('Nie znaleziono turnieju')
            draft_enabled = tournament['rules_version'] == '2'
            entries = c.execute("SELECT e.model_id,e.version_id,m.name FROM tournament_entries e JOIN league_models m ON m.id=e.model_id WHERE e.tournament_id=?", (tournament_id,)).fetchall()
            players = {r['model_id']: {'model_id': r['model_id'], 'name': r['name'], 'version_id': r['version_id']} for r in entries}
            registry_names_by_filename = {}
            runtime_context = {'white': None, 'black': None}
            budget_snapshots = league.prepare_tournament_budgets(tournament_id)
            for model_id, player in players.items():
                manifest = _version_record(player['version_id'])
                for filename_key in ('filename', 'draft_filename'):
                    filename = manifest.get(filename_key)
                    if filename:
                        registry_names_by_filename[Path(filename).name] = player['name']
                player['base_inference'] = league.normalize_inference_config(
                    json.loads(manifest.get('metadata_json') or '{}'))
                player['inference'] = dict(player['base_inference'])
                player['inference']['max_tokens'] = budget_snapshots[model_id]
                player['thinking'] = player['inference']['thinking']
                player['model_sha256'] = manifest.get('sha256')
                player['draft_sha256'] = manifest.get('draft_sha256')
            pairings = c.execute("SELECT id,white_model_id,black_model_id FROM tournament_pairings WHERE tournament_id=? AND status='pending' AND stage='league' ORDER BY id", (tournament_id,)).fetchall()
            if any(row['white_model_id'] == row['black_model_id'] for row in pairings):
                raise RuntimeError('Nieprawidłowa para turniejowa: model nie może grać przeciwko samemu sobie')
            if not pairings and c.execute("SELECT COUNT(*) FROM tournament_entries WHERE tournament_id=?", (tournament_id,)).fetchone()[0] < 2:
                raise RuntimeError('Turniej nie ma wystarczającej liczby zawodników')
            c.execute("UPDATE tournaments SET status='running' WHERE id=?", (tournament_id,))
        set_live('starting','Przygotowanie turnieju', tournament_id=tournament_id)
        def publish_runtime_status(stage, message, **fields):
            runtime_label = fields.get('model')
            if runtime_label:
                fields['model'] = _registry_display_name_for_runtime_model(
                    runtime_label, registry_names_by_filename)
            message = _registry_runtime_message(message, registry_names_by_filename)
            if runtime_context['white'] and runtime_context['black']:
                fields.setdefault('white', runtime_context['white'])
                fields.setdefault('black', runtime_context['black'])
            set_live(stage, message, **fields)

        tournament_runtime = engine_runner.LlamafileRuntime(on_status=publish_runtime_status)

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
                    answer = passkey_test.ask_passkey_question(tournament_runtime, player['path'], question, tournament_stop_event, draft_model_path=player.get('draft_path'), enable_thinking=player.get('thinking', False), temperature=player.get('inference', {}).get('temperature', 0.0), max_tokens=1800 if player.get('thinking') else 900)
                    with sqlite3.connect(DB) as db:
                        db.execute("INSERT OR REPLACE INTO passkey_test_answers (tournament_id,model_id,model_name,question_no,question,status,answer,elapsed_seconds,created_at,question_fingerprint) VALUES(?,?,?,?,?,?,?,?,?,?)",
                            (tournament_id, player_id, player['name'], question_no, question,
                             answer['status'], answer['answer'], answer['seconds'],
                             time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                             passkey_test.question_fingerprint(question)))
            finally:
                tournament_runtime.stop()
            if tournament_stop_event.is_set():
                break

        game_runner = engine_runner.SequentialChessRunner(tournament_runtime)
        while True:
            if tournament_stop_event.is_set():
                break
            with league.connect() as c:
                league_pairing = c.execute("SELECT id,white_model_id,black_model_id,'league' AS stage FROM tournament_pairings WHERE tournament_id=? AND status='pending' AND stage='league' ORDER BY id LIMIT 1", (tournament_id,)).fetchone()
            if league_pairing:
                pairing = dict(league_pairing)
            else:
                if not draft_enabled:
                    break
                pairing = league.next_draft_match(tournament_id)
                if pairing is None:
                    break
            white, black = _resolve_pairing_players(players, pairing)
            runtime_context['white'] = white['name']
            runtime_context['black'] = black['name']
            set_live('game_prepare',f'Przygotowanie partii: {white["name"]} vs {black["name"]}', white=white['name'], black=black['name'], pairing_id=pairing['id'], moves=[], fen='rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1', last_move=None, response=None, proposed_move=None, legal_moves=[])
            white_assets, black_assets = _prepare_game_models(white, black)
            set_live('game_ready',f'Partia gotowa: {white["name"]} vs {black["name"]}', white=white['name'], black=black['name'], pairing_id=pairing['id'])
            game_white = {**white, **white_assets}; game_black = {**black, **black_assets}
            game_started = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
            set_live('game_running','Rozpoczęto partię', white=white['name'], black=black['name'], pairing_id=pairing['id'], moves=[], fen='rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1', last_move=None)
            def publish_move(move):
                set_live('move_played', f"Ruch {move['ply']}: {move['san']} ({move['uci']})",
                         model=move['model'], color=move['color'], white=white['name'], black=black['name'], pairing_id=pairing['id'],
                         fen=move.get('fen'), last_move=move, moves=live_moves_snapshot(move))
                observations = move.get('budget_observations') or []
                player = players.get(move.get('model_id'))
                if player and observations:
                    try:
                        league.record_token_budget_observations(
                            tournament_id, move['model_id'], player['model_sha256'],
                            player['draft_sha256'], player['base_inference'], observations)
                    except Exception as budget_error:
                        set_live('token_budget_record_error',
                                 'Nie zapisano obserwacji limitu tokenów: ' + str(budget_error),
                                 model=move.get('model'))
            try:
                try:
                    result = game_runner.play_game(
                        game_white, game_black, tournament_stop_event,
                        progress=publish_move)
                except engine_runner.RunnerError as exc:
                    failed_player = players.get(exc.model_id)
                    if failed_player and exc.budget_observations:
                        league.record_token_budget_observations(
                            tournament_id, exc.model_id, failed_player['model_sha256'],
                            failed_player['draft_sha256'], failed_player['base_inference'],
                            exc.budget_observations)
                    raise
            finally:
                tournament_runtime.stop()
            if result['status'] == 'stopped':
                break
            game_ended = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
            with sqlite3.connect(DB) as db:
                cur = db.execute("INSERT INTO games(started_at,ended_at,white_model,black_model,result,pgn,move_count,illegal_attempts,avg_move_seconds,tournament_id,pairing_id,move_times_json,inference_metadata_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (game_started, game_ended, white['name'], black['name'], result['result'], result['pgn'],
                     len(result['moves']), 0, result['avg_move_seconds'], tournament_id, pairing['id'],
                     json.dumps(result['move_times']), json.dumps(result['moves'], ensure_ascii=False)))
                game_id = cur.lastrowid
            with league.connect() as c:
                c.execute("UPDATE tournament_pairings SET status='completed',game_id=? WHERE id=?", (game_id, pairing['id']))
            if pairing.get('stage') == 'draft':
                league.record_draft_result(pairing['id'], result['result'])
                champion = league.draft_champion(tournament_id)
                if champion:
                    set_live('draft_champion', f'Zwycięzca draftu: {players[champion]["name"]}', tournament_id=tournament_id, champion=players[champion]['name'])
            set_live('game_finished',f'Partia zakończona: {result["result"]}', white=white['name'], black=black['name'], result=result['result'], game_id=game_id, move_count=len(result['moves']), fen=result['fen'], moves=result['moves'], generation_started_at=None, stage=pairing.get('stage','league'))
        with league.connect() as c:
            pending = c.execute("SELECT COUNT(*) FROM tournament_pairings WHERE tournament_id=? AND status='pending'", (tournament_id,)).fetchone()[0]
            status = 'stopped' if tournament_stop_event.is_set() else ('completed' if pending == 0 else 'failed')
            c.execute('UPDATE tournaments SET status=? WHERE id=?', (status, tournament_id))
        set_live('completed' if status == 'completed' else status, 'Turniej zakończony' if status == 'completed' else f'Turniej: {status}', tournament_id=tournament_id)
    except Exception as exc:
        tournament_error = str(exc)
        if isinstance(exc, TournamentPreflightError):
            set_live('blocked','Start turnieju zablokowany przez preflight: '+str(exc),
                     error=str(exc), tournament_id=tournament_id)
        else:
            diagnostics = {}
            if isinstance(exc, engine_runner.RunnerError):
                diagnostics = {
                    'model': exc.model_name,
                    'color': exc.color,
                    'fen': exc.fen,
                    'legal_moves': exc.legal_moves,
                    'uci_attempts': exc.uci_attempts,
                }
            set_live('error','Błąd turnieju: '+str(exc), error=str(exc), **diagnostics)
            with league.connect() as c:
                c.execute("UPDATE tournaments SET status='failed' WHERE id=?", (tournament_id,))
    finally:
        try:
            if tournament_runtime:
                try:
                    tournament_runtime.shutdown()
                except Exception as cleanup_exc:
                    tournament_error = (tournament_error + '; ' if tournament_error else '') + 'Runner shutdown failed: ' + str(cleanup_exc)
            tournament_runtime = None
            try:
                cleanup_unneeded_models()
            except Exception as cleanup_exc:
                tournament_error = (tournament_error + '; ' if tournament_error else '') + 'Model cleanup deferred: ' + str(cleanup_exc)
        finally:
            model_store.MODEL_LIFECYCLE_LOCK.release()


def start_tournament(tournament_id):
    global tournament_thread, tournament_error
    with lock:
        if tournament_thread and tournament_thread.is_alive():
            raise RuntimeError('Inny turniej jest już uruchomiony')
        with league.connect() as c:
            row = c.execute('SELECT status FROM tournaments WHERE id=?', (tournament_id,)).fetchone()
            if not row:
                raise ValueError('Nie znaleziono turnieju')
            if row['status'] not in ('planned', 'stopped', 'failed'):
                raise RuntimeError(f'Nie można uruchomić turnieju ze statusem: {row["status"]}')
            entries_count = c.execute('SELECT COUNT(*) FROM tournament_entries WHERE tournament_id=?', (tournament_id,)).fetchone()[0]
            pairings = c.execute("SELECT COUNT(*) FROM tournament_pairings WHERE tournament_id=? AND status='pending'", (tournament_id,)).fetchone()[0]
        if entries_count < 2 or pairings < 1:
            raise RuntimeError('Turniej nie ma co najmniej dwóch uczestników i oczekujących par')
        # Serialize preflight against manual cleanup. The worker repeats it after
        # acquiring the same lifecycle lock to close the start/cleanup race.
        with model_store.MODEL_LIFECYCLE_LOCK:
            preflight = tournament_preflight(tournament_id)
        if not preflight['ready']:
            raise TournamentPreflightError(_preflight_error_message(preflight))
        tournament_stop_event.clear()
        tournament_error = ''
        tournament_thread = threading.Thread(target=tournament_loop, args=(tournament_id,), daemon=True)
        tournament_thread.start()
        return {'id': tournament_id, 'status': 'starting',
                'entrants': len(preflight['entrants']),
                'runtime_available': preflight['runtime_available']}


def state():
    outcome = board.outcome(claim_draw=True)
    try:
        mem = {}
        for line in Path('/proc/meminfo').read_text().splitlines():
            if line.startswith(('MemAvailable:', 'MemTotal:')):
                k, v = line.split(':', 1); mem[k] = int(v.strip().split()[0]) // 1024
    except Exception:
        mem = {}
    return {
        'fen': board.fen(), 'moves': moves[:], 'turn': 'white' if board.turn else 'black',
        'game_over': bool(outcome), 'result': outcome.result() if outcome else None,
        'termination': outcome.termination.name if outcome else None,
        'models': models(), 'active_model': active_model(), 'llm_online': llm_health(), 'running': running,
        'illegal_attempts': illegal_moves,
        'avg_move_seconds': round(sum(timings)/len(timings), 3) if timings else None,
        'last_move': last_move, 'error': last_error, 'memory_mb': mem,
        'games_saved': sqlite3.connect(DB).execute('SELECT COUNT(*) FROM games').fetchone()[0],
        'tournament_error': tournament_error,
        'tournament_running': bool(tournament_thread and tournament_thread.is_alive()),
        'live': get_live()
    }


def do_step():
    global illegal_moves, last_error, last_move
    with lock:
        if board.is_game_over(claim_draw=True):
            finish_game(); return
        model = active_model()
        if not model:
            last_error = 'Brak pliku GGUF w katalogu modeli.'
            raise RuntimeError(last_error)
        if not llm_health():
            last_error = 'Lokalny serwer modelu nie odpowiada na 127.0.0.1:8088.'
            raise RuntimeError(last_error)
        t0 = time.monotonic()
        try:
            move = ask_model(model, board)
        except Exception as e:
            illegal_moves += 1
            last_error = str(e)
            raise
        elapsed = time.monotonic() - t0
        san = board.san(move)
        board.push(move)
        moves.append({'uci': move.uci(), 'san': san, 'by': 'white' if not board.turn else 'black', 'seconds': round(elapsed, 3)})
        timings.append(elapsed)
        last_move = move.uci()
        last_error = ''
        if board.is_game_over(claim_draw=True):
            finish_game()


def autoplay_loop():
    global running, last_error
    while True:
        with lock:
            active = running
        if not active:
            break
        try:
            do_step()
        except Exception:
            with lock:
                running = False
            break
        time.sleep(0.15)


def random_hf_model():
    """Return one randomly selected GGUF candidate from varied public model families."""
    import random
    queries = ['Qwen GGUF', 'Llama GGUF', 'Gemma GGUF', 'Phi GGUF',
               'Mistral GGUF', 'SmolLM GGUF', 'DeepSeek GGUF', 'GLM GGUF']
    randomizer = random.SystemRandom()
    randomizer.shuffle(queries)
    for query in queries:
        candidates = search_hf_models(query)
        if candidates:
            return [randomizer.choice(candidates)]
    return []


def search_hf_models(query):
    import urllib.parse, urllib.request
    query = (query or '').strip()[:100]
    if len(query) < 2:
        raise ValueError('Wpisz co najmniej 2 znaki')
    params = urllib.parse.urlencode([('search',query),('filter','gguf'),('sort','downloads'),('direction','-1'),('limit','12'),('expand[]','siblings'),('expand[]','cardData')])
    headers={'User-Agent':'LocalAIChessArena/0.1','Accept':'application/json'}
    req=urllib.request.Request('https://huggingface.co/api/models?'+params,headers=headers)
    with urllib.request.urlopen(req,timeout=12) as response:
        data=json.loads(response.read(2_000_000))
    results=[]
    for item in data:
        repo=item.get('id') or ''
        if not repo: continue
        card=item.get('cardData') or {}
        tags=item.get('tags') or []
        license_name=card.get('license') or next((t.split(':',1)[1] for t in tags if t.startswith('license:')),'')
        tree_url='https://huggingface.co/api/models/'+urllib.parse.quote(repo,safe='/')+'/tree/main?recursive=true&expand=true'
        try:
            with urllib.request.urlopen(urllib.request.Request(tree_url,headers=headers),timeout=10) as response:
                tree=json.loads(response.read(4_000_000))
        except Exception:
            tree=[]
        files=[]; draft_files=[]
        for f in tree:
            name=f.get('path') or ''
            base=name.rsplit('/',1)[-1].lower()
            if f.get('type')!='file' or not name.lower().endswith('.gguf'): continue
            lfs=f.get('lfs') or {}
            asset={'filename':name,'size_bytes':lfs.get('size') or f.get('size'),'sha256':lfs.get('oid') if len(lfs.get('oid') or '')==64 else None}
            if __import__('re').search(r'(?:^|[-_])draft[-_].*\.gguf$', base):
                draft_files.append(asset); continue
            if any(part.lower().startswith(('mmproj','imatrix','mtp-','patch-','adapter','projector')) for part in name.split('/')) or __import__('re').search(r'\d{5}-of-\d{5}\.gguf$',base): continue
            files.append(asset)
        for asset in files:
            base=asset['filename'].rsplit('/',1)[-1]
            lower=base.lower()
            expected=lower.replace('nomtp-','draft-',1) if 'nomtp-' in lower else ''
            companions=[d for d in draft_files if d['filename'].rsplit('/',1)[-1].lower()==expected]
            if companions:
                asset['draft_models']=sorted(companions,key=lambda d:d['filename'].lower())
        files.sort(key=lambda f:(f['size_bytes'] is None,f['size_bytes'] or 10**30,f['filename']))
        if files:
            results.append({'repo_id':repo,'name':repo,'downloads':item.get('downloads',0),'likes':item.get('likes',0),'license':license_name or 'nieustalona','source':'https://huggingface.co/'+repo,'files':files[:8]})
    return results


def fetch_hf_license_evidence(source):
    """Fetch pinned HF metadata plus bounded README/LICENSE excerpts; never approve."""
    import urllib.parse
    from urllib.error import HTTPError, URLError
    parsed = urllib.parse.urlparse((source or '').strip())
    if parsed.scheme != 'https' or parsed.hostname != 'huggingface.co' or parsed.port or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('Automatyczne pobieranie dowodów obsługuje wyłącznie repozytoria huggingface.co przez HTTPS')
    parts = [part for part in parsed.path.split('/') if part]
    if len(parts) != 2 or any(part in ('.', '..') for part in parts):
        raise ValueError('Źródło musi wskazywać dokładnie repozytorium autora/modelu na Hugging Face')
    repo = '/'.join(parts)
    headers = {'User-Agent': 'LocalAIChessArena/0.1', 'Accept': 'application/json'}
    api_url = 'https://huggingface.co/api/models/' + urllib.parse.quote(repo, safe='/') + '?expand%5B%5D=siblings'
    req = urllib.request.Request(api_url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=12) as response:
            if urllib.parse.urlparse(response.geturl()).hostname != 'huggingface.co':
                raise ValueError('Nieoczekiwane przekierowanie podczas pobierania metadanych')
            card = json.loads(response.read(1_000_000))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise ValueError(f'Nie udało się pobrać metadanych repozytorium Hugging Face: {exc}') from exc
    card_data = card.get('cardData') or {}
    tags = card.get('tags') or []
    license_name = card_data.get('license') or next((t.split(':', 1)[1] for t in tags if isinstance(t, str) and t.startswith('license:')), '')
    license_url = card_data.get('license_link') or ''
    siblings = card.get('siblings') or []
    filenames = sorted({item.get('rfilename') for item in siblings if isinstance(item, dict) and isinstance(item.get('rfilename'), str)})
    license_files = [name for name in filenames if name.split('/')[-1].lower() in ('license', 'license.md', 'license.txt', 'copying', 'copying.md', 'notice', 'notice.md')]
    readme_file = next((name for name in filenames if name.lower() == 'readme.md'), None)
    revision = card.get('sha') if isinstance(card.get('sha'), str) and re.fullmatch(r'[0-9a-fA-F]{40}', card.get('sha')) else 'main'

    def fetch_document(filename, limit):
        quoted_path = '/'.join(urllib.parse.quote(part, safe='') for part in filename.split('/'))
        raw_url = 'https://huggingface.co/' + repo + '/raw/' + revision + '/' + quoted_path
        request = urllib.request.Request(raw_url, headers={'User-Agent': 'LocalAIChessArena/0.1', 'Accept': 'text/plain'})
        try:
            with urllib.request.urlopen(request, timeout=12) as response:
                if urllib.parse.urlparse(response.geturl()).hostname != 'huggingface.co':
                    raise ValueError('Nieoczekiwane przekierowanie dokumentu poza huggingface.co')
                payload = response.read(limit + 1)
            truncated = len(payload) > limit
            payload = payload[:limit]
            decoded = payload.decode('utf-8', errors='replace')
            return {
                'path': filename, 'url': raw_url, 'sha256': hashlib.sha256(payload).hexdigest(),
                'bytes': len(payload), 'truncated': truncated, 'excerpt': decoded
            }
        except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
            return {'path': filename, 'url': raw_url, 'fetch_error': str(exc)[:300]}

    documents = []
    if readme_file:
        documents.append(fetch_document(readme_file, 32768))
    for filename in license_files[:3]:
        documents.append(fetch_document(filename, 65536))
    successful_docs = [doc for doc in documents if 'excerpt' in doc]
    evidence = {
        'provider': 'Hugging Face', 'repo_id': repo, 'repo_url': 'https://huggingface.co/' + repo,
        'api_url': api_url, 'revision': revision,
        'readme_url': 'https://huggingface.co/' + repo + '/blob/' + revision + '/README.md',
        'license': license_name or None, 'license_link': license_url or None,
        'license_files': ['https://huggingface.co/' + repo + '/blob/' + revision + '/' + '/'.join(urllib.parse.quote(part, safe='') for part in filename.split('/')) for filename in license_files],
        'license_name': card_data.get('license_name'), 'base_model': card_data.get('base_model'),
        'tags': [t for t in tags if isinstance(t, str) and t.startswith('license:')],
        'documents': documents,
        'fetched_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'evidence_status': 'metadata_and_documents_found' if license_name and successful_docs else ('documents_found_metadata_missing' if successful_docs else ('metadata_found' if license_name else 'license_not_declared')),
        'approval_note': 'Treść dokumentów jest dowodem źródłowym, nie opinią prawną ani automatyczną zgodą. Weryfikacja wymaga ludzkiego przeglądu warunków.'
    }
    return evidence

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass
    def send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code); self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
    def do_GET(self):
        if self.path == '/api/models/random':
            try:
                self.send_json(random_hf_model())
            except Exception as e: self.send_json({'error':str(e)},422)
        elif self.path.startswith('/api/models/search'):
            try:
                from urllib.parse import urlparse, parse_qs
                query = parse_qs(urlparse(self.path).query).get('q',[''])[0]
                self.send_json(search_hf_models(query))
            except Exception as e:
                self.send_json({'error':str(e)}, 422)
        elif self.path == '/api/state':
            with lock: self.send_json(state())
        elif self.path == '/api/live':
            self.send_json(get_live())
        elif self.path == '/api/games':
            with sqlite3.connect(DB) as c:
                rows = c.execute('SELECT id,started_at,white_model,black_model,result,move_count,avg_move_seconds FROM games ORDER BY id DESC LIMIT 30').fetchall()
            self.send_json([dict(id=x[0],started_at=x[1],white=x[2],black=x[3],result=x[4],moves=x[5],avg_seconds=round(x[6],3)) for x in rows])
        elif self.path == '/api/models':
            with league.connect() as c:
                rows = c.execute('''SELECT m.id,m.name,m.format,m.active,v.id AS version_id,v.filename,v.sha256,v.size_bytes,v.status,v.license,v.license_verified,v.source,v.managed_copy,v.draft_filename,v.draft_sha256,v.draft_size_bytes,v.draft_status,v.draft_managed_copy,v.metadata_json
                  FROM league_models m LEFT JOIN model_versions v ON v.id=(SELECT MAX(id) FROM model_versions WHERE model_id=m.id)
                  ORDER BY m.name''').fetchall()
            models = []
            for row in rows:
                item = dict(row)
                try:
                    metadata = json.loads(item.pop('metadata_json') or '{}')
                    item['license_evidence'] = metadata.get('license_evidence')
                    item['inference'] = league.normalize_inference_config(metadata)
                    item['token_budget'] = league.token_budget_info(
                        item.get('sha256'), item.get('draft_sha256'), metadata)
                except (TypeError, json.JSONDecodeError, ValueError):
                    item['license_evidence'] = None
                    item['inference'] = {'thinking': False}
                    item.pop('metadata_json', None)
                models.append(item)
            self.send_json(models)
        elif self.path == '/api/league':
            self.send_json(league.elo_ratings('PURE_AI'))
        elif self.path == '/api/league/engine':
            self.send_json(league.elo_ratings('AI_ENGINE'))
        elif self.path == '/api/storage':
            status = league.storage_status()
            managed = model_store.MANAGED_DIR
            managed.mkdir(mode=0o700, parents=True, exist_ok=True)
            status['managed_directory'] = str(managed)
            status['managed_models'] = [
                {'filename': p.name, 'size_bytes': p.stat().st_size}
                for p in sorted(managed.glob('*.gguf')) if p.is_file()
            ]
            status['managed_free_bytes'] = __import__('shutil').disk_usage(managed).free
            self.send_json(status)
        elif self.path.startswith('/api/tournaments/') and self.path.endswith('/preflight'):
            try:
                tournament_id = int(self.path.split('/')[3])
                with league.connect() as c:
                    exists = c.execute('SELECT 1 FROM tournaments WHERE id=?', (tournament_id,)).fetchone()
                if not exists:
                    self.send_json({'error': 'Nie znaleziono turnieju'}, 404)
                else:
                    self.send_json(tournament_preflight(tournament_id))
            except (ValueError, IndexError):
                self.send_json({'error': 'Nieprawidłowy identyfikator turnieju'}, 422)
        elif self.path == '/api/tournaments':
            with league.connect() as c:
                rows = c.execute('''SELECT t.id,t.name,t.format,t.status,t.created_at,
                    (SELECT COUNT(*) FROM tournament_pairings p WHERE p.tournament_id=t.id AND p.stage='league') AS games_planned,
                    (SELECT COUNT(*) FROM tournament_pairings p WHERE p.tournament_id=t.id AND p.stage='draft') AS draft_games_played_or_planned,
                    (SELECT COUNT(*) FROM tournament_entries e WHERE e.tournament_id=t.id) AS players,
                    t.qualification_percent,
                    CASE WHEN t.rules_version='2' THEN
                      (SELECT COUNT(*) FROM tournament_entries e WHERE e.tournament_id=t.id) *
                      ((SELECT COUNT(*) FROM tournament_entries e WHERE e.tournament_id=t.id)-1) +
                      MAX(1,COALESCE((SELECT COUNT(*) FROM tournament_seeds q WHERE q.tournament_id=t.id),CAST(((SELECT COUNT(*) FROM tournament_entries e WHERE e.tournament_id=t.id)*t.qualification_percent+99)/100 AS INTEGER)))-1
                    ELSE (SELECT COUNT(*) FROM tournament_pairings p WHERE p.tournament_id=t.id) END AS total_games_planned
                    FROM tournaments t ORDER BY t.id DESC''').fetchall()
            self.send_json([dict(x) for x in rows])
        elif self.path.startswith('/api/passkey-tests'):
            from urllib.parse import urlparse, parse_qs
            tid = parse_qs(urlparse(self.path).query).get('tournament_id', [None])[0]
            with sqlite3.connect(DB) as c:
                c.row_factory = sqlite3.Row
                rows = c.execute('SELECT tournament_id,model_id,model_name,question_no,question,status,answer,elapsed_seconds,created_at FROM passkey_test_answers' + (' WHERE tournament_id=?' if tid else '') + ' ORDER BY tournament_id,model_name,question_no', ((int(tid),) if tid else ())).fetchall()
            self.send_json([dict(x) for x in rows])
        elif self.path == '/api/export/pgn':
            with sqlite3.connect(DB) as c:
                rows = c.execute('SELECT pgn FROM games ORDER BY id').fetchall()
            body = ('\n\n'.join(row[0].strip() for row in rows if row[0].strip()) + ('\n' if rows else '')).encode('utf-8')
            self.send_response(200); self.send_header('Content-Type', 'application/x-chess-pgn; charset=utf-8')
            self.send_header('Content-Disposition', 'attachment; filename="ai-chess-arena.pgn"')
            self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
        elif self.path == '/api/export/csv':
            output = io.StringIO(newline='')
            writer = csv.writer(output)
            writer.writerow(['id','started_at','ended_at','white_model','black_model','result','move_count','illegal_attempts','avg_move_seconds','pgn'])
            with sqlite3.connect(DB) as c:
                for row in c.execute('SELECT id,started_at,ended_at,white_model,black_model,result,move_count,illegal_attempts,avg_move_seconds,pgn FROM games ORDER BY id'):
                    writer.writerow(row)
            body = output.getvalue().encode('utf-8-sig')
            self.send_response(200); self.send_header('Content-Type', 'text/csv; charset=utf-8')
            self.send_header('Content-Disposition', 'attachment; filename="ai-chess-arena.csv"')
            self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
        elif self.path == '/':
            data = (ROOT/'index.html').read_bytes(); self.send_response(200)
            self.send_header('Content-Type','text/html; charset=utf-8'); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data)
        else: self.send_error(404)
    def do_POST(self):
        global board, moves, timings, illegal_moves, running, started_at, last_error, last_move
        if self.path in ('/api/models/register','/api/models/license-evidence','/api/models/verify-license','/api/tournaments','/api/models/download','/api/models/cleanup','/api/tournaments/start','/api/tournaments/stop'):
            try:
                length = int(self.headers.get('Content-Length','0'))
                payload = json.loads(self.rfile.read(length) or b'{}')
                if self.path == '/api/models/license-evidence':
                    version_id = int(payload.get('version_id', 0))
                    if version_id < 1:
                        raise ValueError('Nieprawidłowy identyfikator wersji modelu')
                    with league.connect() as c:
                        row = c.execute('SELECT id,source FROM model_versions WHERE id=?', (version_id,)).fetchone()
                    if row is None:
                        raise ValueError('Nieznana wersja modelu')
                    evidence = fetch_hf_license_evidence(row['source'])
                    with league.connect() as c:
                        current = c.execute('SELECT metadata_json FROM model_versions WHERE id=?', (version_id,)).fetchone()
                        if current is None:
                            raise ValueError('Wersja modelu została usunięta podczas pobierania dowodów')
                        metadata = json.loads(current['metadata_json'] or '{}')
                        metadata['license_evidence'] = evidence
                        c.execute('UPDATE model_versions SET metadata_json=? WHERE id=?', (json.dumps(metadata, ensure_ascii=False), version_id))
                    result = {'version_id': version_id, 'evidence': evidence, 'license_verified': False}
                elif self.path == '/api/models/verify-license':
                    if payload.get('confirm_review') is not True:
                        raise ValueError('Wymagane jawne potwierdzenie samodzielnego sprawdzenia licencji')
                    version_id = int(payload.get('version_id', 0))
                    if version_id < 1:
                        raise ValueError('Nieprawidłowy identyfikator wersji modelu')
                    with league.connect() as c:
                        row = c.execute('SELECT id,source,license,metadata_json FROM model_versions WHERE id=?', (version_id,)).fetchone()
                        if row is None:
                            raise ValueError('Nieznana wersja modelu')
                        source = (row['source'] or '').strip()
                        license_name = (payload.get('license') or row['license'] or '').strip()
                        metadata = json.loads(row['metadata_json'] or '{}')
                        evidence = metadata.get('license_evidence') or {}
                        has_license_document = bool(evidence.get('license_files') or evidence.get('license'))
                        if not source or not source.startswith('https://') or not license_name or license_name.lower() in ('nieustalona','unknown','n/a','unverified','unspecified','none'):
                            raise ValueError('Wersja wymaga źródła HTTPS i konkretnego identyfikatora licencji')
                        if not row['license'] and not has_license_document:
                            raise ValueError('Najpierw pobierz dowody z repozytorium; nie znaleziono deklaracji ani pliku licencji')
                        c.execute('UPDATE model_versions SET license=?,license_verified=1 WHERE id=?', (license_name, version_id))
                    result = {'version_id': version_id, 'license_verified': True}
                elif self.path == '/api/models/register':
                    result = league.register_model(payload.get('name',''),payload.get('filename'),payload.get('source',''),payload.get('license',''),payload.get('metadata'),payload.get('format','PURE_AI'),bool(payload.get('license_verified',False)))
                elif self.path == '/api/tournaments/start':
                    result = start_tournament(int(payload.get('tournament_id', 0)))
                elif self.path == '/api/tournaments/stop':
                    tournament_stop_event.set()
                    result = {'status': 'stop_requested'}
                elif self.path == '/api/models/download':
                    if payload.get('confirm_download') is not True:
                        raise ValueError('Wymagane jawne potwierdzenie pobrania')
                    result = model_store.download_version(
                        int(payload.get('version_id',0)), payload.get('url',''), payload.get('filename',''),
                        payload.get('sha256',''), int(payload.get('size_bytes',0)),
                        bool(payload.get('license_verified',False)))
                elif self.path == '/api/models/cleanup':
                    if payload.get('confirm_delete') is not True:
                        raise ValueError('Wymagane jawne potwierdzenie usunięcia kopii areny')
                    if not model_store.MODEL_LIFECYCLE_LOCK.acquire(blocking=False):
                        raise RuntimeError('Nie można usuwać modeli podczas aktywnego turnieju lub pobierania; spróbuj później')
                    try:
                        result = model_store.cleanup_version(int(payload.get('version_id',0)))
                    finally:
                        model_store.MODEL_LIFECYCLE_LOCK.release()
                else:
                    result = league.create_round_robin(payload.get('name','Tournament'),payload.get('format','PURE_AI'),payload.get('qualification_percent',80))
                self.send_json(result, 200 if self.path in ('/api/models/verify-license','/api/models/license-evidence') else 201)
            except Exception as e:
                self.send_json({'error':str(e)},422)
        elif self.path == '/api/step':
            try:
                with lock: do_step(); self.send_json(state())
            except Exception as e:
                with lock: self.send_json({'error':str(e), **state()}, 422)
        elif self.path == '/api/run':
            with lock:
                running = True
                threading.Thread(target=autoplay_loop, daemon=True).start()
                self.send_json(state())
        elif self.path == '/api/stop':
            with lock: running = False; self.send_json(state())
        elif self.path == '/api/reset':
            with lock:
                running = False; board = chess.Board(); moves = []; timings = []; illegal_moves = 0
                started_at = time.time(); last_error = ''; last_move = None
                self.send_json(state())
        else: self.send_error(404)

if __name__ == '__main__':
    db_init()
    _prune_jsonl(LIVE_EVENTS_LOG, LIVE_EVENT_RETENTION_SECONDS, 500)
    _prune_jsonl(MODEL_RESPONSES_LOG, MODEL_RESPONSE_RETENTION_SECONDS, 200)
    print(f'Local AI Chess Arena: http://{HOST}:{PORT}', flush=True)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
