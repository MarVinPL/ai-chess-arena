#!/usr/bin/env python3
import csv, hashlib, io, json, os, re, shutil, sqlite3, threading, time, urllib.request, urllib.error
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

def set_live(stage, message, **fields):
    with live_lock:
        now = time.time()
        if stage == 'generating':
            fields.setdefault('generation_started_at', now)
        elif 'generation_started_at' not in fields:
            fields['generation_started_at'] = None
        live_status.update({'stage':stage,'message':message,'updated_at':now,**fields})
        event_fields = ('model','color','response','proposed_move','last_move','pairing_id','question_no','error','result')
        live_status['events'] = ([{'at':time.strftime('%H:%M:%S', time.localtime(now)),'stage':stage,'message':message,**{k:v for k,v in fields.items() if k in event_fields}}] + live_status.get('events',[]))[:40]

def get_live():
    with live_lock:
        return dict(live_status)



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
    model_store.MODEL_LIFECYCLE_LOCK.acquire()
    try:
        with league.connect() as c:
            tournament = c.execute('SELECT * FROM tournaments WHERE id=?', (tournament_id,)).fetchone()
            if tournament is None:
                raise RuntimeError('Nie znaleziono turnieju')
            entries = c.execute("SELECT e.model_id,e.version_id,m.name FROM tournament_entries e JOIN league_models m ON m.id=e.model_id WHERE e.tournament_id=?", (tournament_id,)).fetchall()
            players = {r['model_id']: {'name': r['name'], 'version_id': r['version_id']} for r in entries}
            pairings = c.execute("SELECT id,white_model_id,black_model_id FROM tournament_pairings WHERE tournament_id=? AND status='pending' ORDER BY id", (tournament_id,)).fetchall()
            if any(row['white_model_id'] == row['black_model_id'] for row in pairings):
                raise RuntimeError('Nieprawidłowa para turniejowa: model nie może grać przeciwko samemu sobie')
            if not pairings:
                c.execute("UPDATE tournaments SET status='completed' WHERE id=?", (tournament_id,))
                return
            c.execute("UPDATE tournaments SET status='running' WHERE id=?", (tournament_id,))
        set_live('starting','Przygotowanie turnieju', tournament_id=tournament_id)
        tournament_runtime = engine_runner.LlamafileRuntime(on_status=set_live)

        # Passkey checks run per model; its GGUF is removed after the three questions.
        for player_id, player in players.items():
            missing = []
            for question_no, question in enumerate(passkey_test.QUESTIONS, 1):
                with sqlite3.connect(DB) as db:
                    already = db.execute('SELECT 1 FROM passkey_test_answers WHERE tournament_id=? AND model_id=? AND question_no=?',
                                         (tournament_id, player_id, question_no)).fetchone()
                if not already:
                    missing.append((question_no, question))
            if not missing or tournament_stop_event.is_set():
                continue
            set_live('passkey_prepare',f'Przygotowanie testu Passkey: {player["name"]}', model=player['name'])
            assets = _prepare_single_model(player)
            player.update(assets)
            try:
                for question_no, question in missing:
                    set_live('passkey_question',f'Test Passkey: pytanie {question_no}/3', model=player['name'], question_no=question_no)
                    if tournament_stop_event.is_set():
                        break
                    answer = passkey_test.ask_passkey_question(tournament_runtime, player['path'], question, tournament_stop_event, draft_model_path=player.get('draft_path'))
                    with sqlite3.connect(DB) as db:
                        db.execute("INSERT OR REPLACE INTO passkey_test_answers (tournament_id,model_id,model_name,question_no,question,status,answer,elapsed_seconds,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                            (tournament_id, player_id, player['name'], question_no, question,
                             answer['status'], answer['answer'], answer['seconds'],
                             time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())))
            finally:
                tournament_runtime.stop()
                cleanup_unneeded_models()
            if tournament_stop_event.is_set():
                break

        game_runner = engine_runner.SequentialChessRunner(tournament_runtime)
        for pairing in pairings:
            if tournament_stop_event.is_set():
                break
            white = players.get(pairing['white_model_id']); black = players.get(pairing['black_model_id'])
            if not white or not black:
                raise RuntimeError('Brak wersji zawodnika przypisanej do pary turniejowej')
            set_live('game_prepare',f'Przygotowanie partii: {white["name"]} vs {black["name"]}', white=white['name'], black=black['name'], pairing_id=pairing['id'])
            white_assets, black_assets = _prepare_game_models(white, black)
            set_live('game_ready',f'Partia gotowa: {white["name"]} vs {black["name"]}', white=white['name'], black=black['name'], pairing_id=pairing['id'])
            game_white = {**white, **white_assets}; game_black = {**black, **black_assets}
            game_started = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
            set_live('game_running','Rozpoczęto partię', white=white['name'], black=black['name'], pairing_id=pairing['id'], moves=[], fen='rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1', last_move=None)
            try:
                result = game_runner.play_game(
                    game_white, game_black, tournament_stop_event,
                    progress=lambda move: set_live(
                        'move_played', f"Ruch {move['ply']}: {move['san']} ({move['uci']})",
                        model=move['model'], color=move['color'], pairing_id=pairing['id'],
                        fen=move.get('fen'), last_move=move, moves=live_moves_snapshot(move)))
            finally:
                tournament_runtime.stop()
                cleanup_unneeded_models()
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
            set_live('game_finished',f'Partia zakończona: {result["result"]}', white=white['name'], black=black['name'], result=result['result'], game_id=game_id, move_count=len(result['moves']), fen=result['fen'], moves=result['moves'], generation_started_at=None)
        with league.connect() as c:
            pending = c.execute("SELECT COUNT(*) FROM tournament_pairings WHERE tournament_id=? AND status='pending'", (tournament_id,)).fetchone()[0]
            status = 'stopped' if tournament_stop_event.is_set() else ('completed' if pending == 0 else 'failed')
            c.execute('UPDATE tournaments SET status=? WHERE id=?', (status, tournament_id))
        set_live('completed' if status == 'completed' else status, 'Turniej zakończony' if status == 'completed' else f'Turniej: {status}', tournament_id=tournament_id)
    except Exception as exc:
        tournament_error = str(exc)
        set_live('error','Błąd turnieju: '+str(exc), error=str(exc))
        with league.connect() as c:
            c.execute("UPDATE tournaments SET status='failed' WHERE id=?", (tournament_id,))
    finally:
        if tournament_runtime:
            tournament_runtime.stop()
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
            entries = c.execute('''SELECT e.model_id,e.version_id,m.name,v.id,v.filename,v.status,v.license_verified,v.managed_copy
                FROM tournament_entries e JOIN league_models m ON m.id=e.model_id
                JOIN model_versions v ON v.id=e.version_id WHERE e.tournament_id=?''', (tournament_id,)).fetchall()
            pairings = c.execute("SELECT COUNT(*) FROM tournament_pairings WHERE tournament_id=? AND status='pending'", (tournament_id,)).fetchone()[0]
        if len(entries) < 2 or pairings < 1:
            raise RuntimeError('Turniej nie ma co najmniej dwóch zawodników i oczekujących par')
        # Validate immutable manifests now; GGUF files are fetched only for each game.
        for version in entries:
            manifest = _version_record(version['version_id'])
            if manifest['license_verified'] != 1:
                raise RuntimeError('Model bez jawnie zweryfikowanej licencji')
        tournament_stop_event.clear()
        tournament_error = ''
        tournament_thread = threading.Thread(target=tournament_loop, args=(tournament_id,), daemon=True)
        tournament_thread.start()
        return {'id': tournament_id, 'status': 'starting'}


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
    """Fetch provenance evidence from Hugging Face without approving a license."""
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
            if response.geturl().split('/')[2] != 'huggingface.co':
                raise ValueError('Nieoczekiwane przekierowanie podczas pobierania metadanych')
            card = json.loads(response.read(1_000_000))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise ValueError(f'Nie udało się pobrać metadanych repozytorium Hugging Face: {exc}') from exc
    card_data = card.get('cardData') or {}
    tags = card.get('tags') or []
    license_name = card_data.get('license') or next((t.split(':', 1)[1] for t in tags if isinstance(t, str) and t.startswith('license:')), '')
    license_url = card_data.get('license_link') or ''
    siblings = card.get('siblings') or []
    license_files = sorted({item.get('rfilename') for item in siblings if isinstance(item, dict) and item.get('rfilename') and item.get('rfilename').split('/')[-1].lower() in ('license', 'license.md', 'license.txt', 'copying', 'copying.md', 'notice', 'notice.md')})
    evidence = {
        'provider': 'Hugging Face', 'repo_id': repo, 'repo_url': 'https://huggingface.co/' + repo,
        'api_url': api_url, 'revision': card.get('sha'), 'readme_url': 'https://huggingface.co/' + repo + '/blob/' + (card.get('sha') or 'main') + '/README.md',
        'license': license_name or None, 'license_link': license_url or None,
        'license_files': ['https://huggingface.co/' + repo + '/blob/' + (card.get('sha') or 'main') + '/' + '/'.join(urllib.parse.quote(part, safe='') for part in filename.split('/')) for filename in license_files],
        'license_name': card_data.get('license_name'), 'base_model': card_data.get('base_model'),
        'tags': [t for t in tags if isinstance(t, str) and t.startswith('license:')],
        'fetched_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'evidence_status': 'metadata_found' if license_name else 'license_not_declared',
        'approval_note': 'Metadane źródłowe nie stanowią opinii prawnej ani automatycznej zgody na użycie.'
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
                rows = c.execute('SELECT id,started_at,result,move_count,avg_move_seconds FROM games ORDER BY id DESC LIMIT 30').fetchall()
            self.send_json([dict(id=x[0],started_at=x[1],result=x[2],moves=x[3],avg_seconds=round(x[4],3)) for x in rows])
        elif self.path == '/api/models':
            with league.connect() as c:
                rows = c.execute('''SELECT m.id,m.name,m.format,m.active,v.id AS version_id,v.filename,v.sha256,v.size_bytes,v.status,v.license,v.license_verified,v.source,v.managed_copy,v.draft_filename,v.draft_sha256,v.draft_size_bytes,v.draft_status,v.draft_managed_copy,v.metadata_json
                  FROM league_models m LEFT JOIN model_versions v ON v.id=(SELECT MAX(id) FROM model_versions WHERE model_id=m.id)
                  ORDER BY m.name''').fetchall()
            models = []
            for row in rows:
                item = dict(row)
                try:
                    item['license_evidence'] = json.loads(item.pop('metadata_json') or '{}').get('license_evidence')
                except (TypeError, json.JSONDecodeError):
                    item['license_evidence'] = None
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
        elif self.path == '/api/tournaments':
            with league.connect() as c:
                rows = c.execute('SELECT t.id,t.name,t.format,t.status,t.created_at,COUNT(p.id) AS games_planned FROM tournaments t LEFT JOIN tournament_pairings p ON p.tournament_id=t.id GROUP BY t.id ORDER BY t.id DESC').fetchall()
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
                    result = league.create_round_robin(payload.get('name','Tournament'),payload.get('format','PURE_AI'))
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
    print(f'Local AI Chess Arena: http://{HOST}:{PORT}', flush=True)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
