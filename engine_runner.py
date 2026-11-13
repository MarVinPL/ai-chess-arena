"""Sequential GGUF chess inference using one local llamafile process at a time."""
from __future__ import annotations

import json
import re
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

import chess

LLAMAFILE = Path('/home/debian/ai-agent/bin/llamafile')
API_BASE = 'http://127.0.0.1:8088'


class RunnerError(RuntimeError):
    def __init__(self, message, budget_observations=None):
        super().__init__(message)
        self.budget_observations = list(budget_observations or [])
        self.model_id = None
        self.model_name = None
        self.inference = None
        self.model_sha256 = None
        self.draft_sha256 = None


class LlamafileRuntime:
    """Owns exactly one llamafile child process; never kills unrelated processes."""

    def __init__(self, binary: Path = LLAMAFILE, api_base: str = API_BASE,
                 threads: int = 2, context: int = 2048, startup_timeout: float = 90,
                 popen: Callable = subprocess.Popen, on_status: Callable | None = None,
                 monitor_interval: float = 30):
        self.binary = Path(binary)
        self.api_base = api_base.rstrip('/')
        self.threads = max(1, int(threads))
        self.context = max(512, int(context))
        self.startup_timeout = startup_timeout
        self._popen = popen
        self._process = None
        self._model_path: Path | None = None
        self._draft_model_path: Path | None = None
        self._lock = threading.RLock()
        self.on_status = on_status
        self.monitor_interval = max(5.0, float(monitor_interval))
        self._desired_model_path: Path | None = None
        self._desired_draft_model_path: Path | None = None
        self._monitor_stop = threading.Event()
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop, name='llamafile-watchdog', daemon=True)
        self._monitor_thread.start()

    @property
    def loaded_model(self) -> str | None:
        return str(self._model_path) if self._process and self._process.poll() is None and self._model_path else None

    def _notify(self, stage: str, message: str, **fields) -> None:
        if self.on_status:
            try:
                self.on_status(stage, message, **fields)
            except Exception:
                pass

    def _healthy(self) -> bool:
        try:
            req = urllib.request.Request(self.api_base + '/health')
            with urllib.request.urlopen(req, timeout=1.5) as response:
                return response.status == 200
        except Exception:
            return False

    def _monitor_once(self) -> None:
        """Restart only the child this runtime owns, and only while a model is expected."""
        with self._lock:
            model_path = self._desired_model_path
            draft_path = self._desired_draft_model_path
            if model_path is None:
                return
            proc = self._process
            process_alive = proc is not None and proc.poll() is None
            if process_alive and self._healthy():
                return
            self._notify('model_watchdog_restart',
                         'Watchdog wykrył niedostępny llamafile; ponawiam uruchomienie',
                         model=model_path.name)
            try:
                # RLock keeps this check/restart atomic relative to an intentional stop().
                self.ensure_loaded(model_path, draft_path)
            except Exception as exc:
                # A failed recovery attempt must remain eligible for the next
                # watchdog cycle. Preserve the desired model unless stop() was
                # explicitly requested after this recovery attempt.
                if self._desired_model_path is None:
                    self._desired_model_path = model_path
                    self._desired_draft_model_path = draft_path
                self._notify('model_watchdog_error',
                             'Watchdog nie zdołał uruchomić llamafile: ' + str(exc),
                             model=model_path.name, error=str(exc))

    def _monitor_loop(self) -> None:
        while not self._monitor_stop.wait(self.monitor_interval):
            try:
                self._monitor_once()
            except Exception as exc:
                self._notify('model_watchdog_error',
                             'Błąd watchdog llamafile: ' + str(exc), error=str(exc))

    def stop(self) -> None:
        with self._lock:
            # Clearing the desired model disables watchdog restarts for intentional stops.
            self._desired_model_path = None
            self._desired_draft_model_path = None
            proc, self._process = self._process, None
            self._model_path = None
            self._draft_model_path = None
            if proc is None:
                return
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)

    def shutdown(self) -> None:
        """Stop the owned process and terminate the watchdog thread at runtime end."""
        self._monitor_stop.set()
        self.stop()
        if threading.current_thread() is not self._monitor_thread:
            self._monitor_thread.join(timeout=self.startup_timeout + 2)

    def ensure_loaded(self, model_path: str | Path, draft_model_path: str | Path | None = None) -> None:
        path = Path(model_path).resolve(strict=True)
        draft_path = Path(draft_model_path).resolve(strict=True) if draft_model_path else None
        if not path.is_file() or path.suffix.lower() != '.gguf':
            raise RunnerError(f'Nieprawidłowy plik modelu GGUF: {path}')
        if draft_path and (not draft_path.is_file() or draft_path.suffix.lower() != '.gguf' or draft_path == path):
            raise RunnerError(f'Nieprawidłowy plik pomocniczy draft GGUF: {draft_path}')
        if not self.binary.is_file() or not self.binary.stat().st_mode & 0o111:
            raise RunnerError(f'Brak wykonywalnego llamafile: {self.binary}')
        with self._lock:
            if self.loaded_model == str(path) and self._draft_model_path == draft_path and self._healthy():
                return
            self.stop()
            self._notify('model_loading', f'Ładowanie modelu: {path.name}', model=path.name)
            # Single server slot and loopback-only binding are intentional.
            # The packaged llamafile on this VPS is a shell-polyglot executable
            # without an ELF header; interactive shells apply ENOEXEC fallback,
            # while subprocess.Popen does not. Invoke that verified wrapper via sh.
            with self.binary.open('rb') as binary_file:
                is_elf = binary_file.read(4) == b'\x7fELF'
            command = [str(self.binary)] if is_elf else ['/bin/sh', str(self.binary)]
            args = command + ['-m', str(path), '--server', '--host', '127.0.0.1',
                    '--port', '8088', '--threads', str(self.threads), '--ctx-size', str(self.context),
                    '--parallel', '1', '--temp', '0', '--no-ui', '--no-warmup']
            if draft_path:
                args += ['--spec-type', 'draft-mtp', '--model-draft', str(draft_path)]
            self._process = self._popen(args, stdin=subprocess.DEVNULL,
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                        start_new_session=True)
            self._model_path = path
            self._draft_model_path = draft_path
            self._desired_model_path = path
            self._desired_draft_model_path = draft_path
            deadline = time.monotonic() + self.startup_timeout
            while time.monotonic() < deadline:
                if self._process.poll() is not None:
                    code = self._process.returncode
                    self._process = None; self._model_path = None; self._draft_model_path = None
                    self._desired_model_path = None; self._desired_draft_model_path = None
                    raise RunnerError(f'llamafile zakończył start kodem {code}')
                if self._healthy():
                    self._notify('model_ready', f'Model gotowy: {path.name}', model=path.name)
                    return
                time.sleep(0.5)
            self.stop()
            raise RunnerError('Przekroczono czas oczekiwania na gotowość llamafile (to nie jest limit czasu ruchu)')

    @staticmethod
    def _allowed_moves(board: chess.Board, recent_moves: list[dict] | None = None) -> tuple[list[chess.Move], list[str]]:
        legal = list(board.legal_moves)
        # Defensive evasions take priority over the anti-oscillation heuristic.
        if board.is_check():
            return legal, []
        blocked = set()
        # Avoid a non-forced immediate reversal by the same piece. Keep a short
        # per-game history so a model cannot alternate a rook/queen between two
        # squares every other turn. Captures and checking moves remain available.
        for record in (recent_moves or [])[-8:]:
            if record.get('color') != ('white' if board.turn == chess.WHITE else 'black'):
                continue
            try:
                previous = chess.Move.from_uci(record.get('uci', ''))
            except (ValueError, TypeError):
                continue
            piece = board.piece_at(previous.to_square)
            if not piece or piece.color != board.turn:
                continue
            expected_piece = record.get('piece')
            if expected_piece and piece.symbol() != expected_piece:
                continue
            reverse = chess.Move(previous.to_square, previous.from_square)
            if reverse in legal and not board.is_capture(reverse) and not board.gives_check(reverse):
                blocked.add(reverse)
        allowed = [move for move in legal if move not in blocked]
        # Never dead-end a position: if every legal move would be a reversal,
        # permit the legal set rather than inventing or forcing an illegal move.
        if not allowed:
            allowed = legal
            blocked.clear()
        return allowed, sorted(move.uci() for move in blocked)

    def generate(self, model_path: str | Path, board: chess.Board,
                 stop_event: threading.Event | None = None,
                 draft_model_path: str | Path | None = None,
                 recent_moves: list[dict] | None = None, enable_thinking: bool = False,
                 temperature: float = 0.0, max_tokens: int = 16) -> tuple[chess.Move, dict]:
        self.ensure_loaded(model_path, draft_model_path)
        if stop_event and stop_event.is_set():
            raise RunnerError('Turniej zatrzymany przez operatora')
        allowed_moves, blocked_reversals = self._allowed_moves(board, recent_moves)
        legal = [move.uci() for move in allowed_moves]
        if not legal:
            raise RunnerError('Pozycja nie zawiera legalnych ruchów')
        color = 'white' if board.turn == chess.WHITE else 'black'
        legal_text = ", ".join(legal)
        anti_loop_note = (' Nie cofaj tej samej figury na poprzednie pole, jeśli masz inną legalną możliwość.'
                          if blocked_reversals else '')
        initial_prompt = (
            'Jesteś graczem szachowym. Wybierz dokładnie jeden ruch z listy LEGAL MOVES. '
            'Zwróć wyłącznie ruch UCI.' + anti_loop_note + '\n'
            f'FEN: {board.fen()}\nLEGAL MOVES: {legal_text}\nMOVE:')
        retry_prompt = None
        self._notify('generating', 'Model generuje propozycję ruchu', model=Path(model_path).name, color=color, fen=board.fen(), legal_moves=legal, response=None)
        started = time.monotonic()
        content = ''
        move = None
        budget_observations = []
        # One bounded retry handles malformed model output without inventing a move.
        # No per-move timeout: user-requested timing is measured, not forfeited.
        for attempt in range(2):
            if attempt and stop_event and stop_event.is_set():
                raise RunnerError('Turniej zatrzymany przez operatora')
            prompt = initial_prompt if attempt == 0 else retry_prompt
            payload = json.dumps({'messages': [{'role': 'user', 'content': prompt}],
                                  'temperature': float(temperature), 'max_tokens': int(max_tokens), 'stream': False,
                                  'chat_template_kwargs': {'enable_thinking': bool(enable_thinking)}}).encode()
            request = urllib.request.Request(self.api_base + '/v1/chat/completions', data=payload,
                                             headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(request, timeout=None) as response:
                data = json.loads(response.read().decode())
            try:
                choice = data['choices'][0]
                content = choice['message'].get('content', '')
                finish_reason = choice.get('finish_reason')
            except (KeyError, IndexError, TypeError) as exc:
                raise RunnerError('Nieprawidłowa odpowiedź API llamafile') from exc
            usage = data.get('usage') if isinstance(data, dict) else None
            completion_tokens = usage.get('completion_tokens') if isinstance(usage, dict) else None
            if isinstance(completion_tokens, bool) or not isinstance(completion_tokens, int) or completion_tokens < 0:
                completion_tokens = None
            if not isinstance(content, str):
                content = str(content or '')
            match = re.search(r'(?<![a-h1-8])([a-h][1-8][a-h][1-8][qrbn]?)(?![a-h1-8])', content.lower())
            illegal_uci = None
            blocked_uci = None
            candidate_valid = False
            if match:
                candidate = chess.Move.from_uci(match.group(1))
                if candidate in allowed_moves:
                    move = candidate
                    candidate_valid = True
                elif candidate in board.legal_moves and candidate.uci() in blocked_reversals:
                    blocked_uci = candidate.uci()
                else:
                    illegal_uci = candidate.uci()
            truncated = (str(finish_reason).lower() in ('length','max_tokens','max_output_tokens')
                         or (completion_tokens is not None and completion_tokens >= int(max_tokens)))
            budget_observations.append({'max_tokens':int(max_tokens),'completion_tokens':completion_tokens,
                'finish_reason':finish_reason,'truncated':bool(truncated),
                'valid_move':bool(candidate_valid),'attempt':attempt+1})
            if candidate_valid:
                break
            if attempt == 0:
                if blocked_uci:
                    feedback = (f'Ruch {blocked_uci} jest legalny w szachach, ale powtarza cofnięcie tej samej figury. '
                                'Wybierz inny ruch z listy LEGAL MOVES.')
                elif illegal_uci:
                    feedback = (f'Twój poprzedni ruch {illegal_uci} jest NIELEGALNY w tej pozycji. '
                                'Nie wolno go wykonać. Wybierz inny ruch WYŁĄCZNIE z poniższej listy.')
                else:
                    feedback = ('Twoja poprzednia odpowiedź nie zawierała poprawnego ruchu UCI. '
                                'Zwróć teraz dokładnie jeden legalny ruch z poniższej listy.')
                retry_prompt = (f'{feedback}\n'
                                f'FEN: {board.fen()}\nLEGAL MOVES: {legal_text}\n'
                                'Nie dodawaj wyjaśnień ani słowa UCI. Odpowiedź musi być jednym z tokenów z listy.\nMOVE:')
                self._notify('retrying', feedback, model=Path(model_path).name,
                             color=color, fen=board.fen(), legal_moves=legal, response=content[:300],
                             illegal_move=illegal_uci, attempt=2)
        elapsed = time.monotonic() - started
        if move is None:
            raise RunnerError(f'Model nie zwrócił legalnego ruchu UCI po 2 próbach: {content[:120]!r}',
                              budget_observations=budget_observations)
        self._notify('move_generated', f'Model zaproponował ruch: {move.uci()}', model=Path(model_path).name, color=color, fen=board.fen(), legal_moves=legal, response=content[:300], proposed_move=move.uci(), elapsed_seconds=round(elapsed,4))
        return move, {'seconds': round(elapsed, 4), 'runtime': 'llamafile',
                      'model_path': str(Path(model_path).resolve()),
                      'draft_model_path': str(Path(draft_model_path).resolve()) if draft_model_path else None,
                      'response': content[:160], 'thinking': bool(enable_thinking),
                      'temperature': float(temperature), 'max_tokens': int(max_tokens),
                      'budget_observations': budget_observations}


class SequentialChessRunner:
    """Play a complete game; model switching happens before each side's turn."""

    def __init__(self, runtime: LlamafileRuntime):
        self.runtime = runtime

    def play_game(self, white: dict, black: dict,
                  stop_event: threading.Event | None = None,
                  progress: Callable[[dict], None] | None = None) -> dict:
        board = chess.Board()
        move_records, times = [], []
        while not board.is_game_over(claim_draw=True):
            if stop_event and stop_event.is_set():
                return {'status': 'stopped', 'fen': board.fen(), 'moves': move_records,
                        'move_times': times}
            player = white if board.turn == chess.WHITE else black
            color = 'white' if board.turn == chess.WHITE else 'black'
            kwargs = {'recent_moves': move_records}
            if player.get('draft_path'):
                kwargs['draft_model_path'] = player['draft_path']
            inference = player.get('inference') or {
                'thinking': bool(player.get('thinking', False)),
                'temperature': 0.0,
                'max_tokens': 512 if player.get('thinking') else 16}
            if inference.get('thinking') is True:
                kwargs['enable_thinking'] = True
            if inference.get('temperature', 0.0) != 0.0:
                kwargs['temperature'] = float(inference['temperature'])
            if inference.get('max_tokens', 16) != 16:
                kwargs['max_tokens'] = int(inference['max_tokens'])
            try:
                move, meta = self.runtime.generate(player['path'], board, stop_event, **kwargs)
            except RunnerError as exc:
                exc.model_id = player.get('model_id')
                exc.model_name = player.get('name')
                exc.inference = dict(inference)
                exc.model_sha256 = player.get('model_sha256')
                exc.draft_sha256 = player.get('draft_sha256')
                raise
            san = board.san(move)
            piece = board.piece_at(move.from_square)
            piece_symbol = piece.symbol() if piece else None
            board.push(move)
            record = {'ply': len(move_records) + 1, 'color': color,
                      'model': player['name'], 'model_id': player.get('model_id'),
                      'thinking': bool(inference.get('thinking', False)),
                      'inference': dict(inference),
                      'budget_observations': meta.get('budget_observations', []),
                      'uci': move.uci(), 'san': san, 'piece': piece_symbol,
                      'seconds': meta['seconds'], 'fen': board.fen()}
            move_records.append(record); times.append(meta['seconds'])
            if progress:
                progress(record)
        outcome = board.outcome(claim_draw=True)
        return {'status': 'finished', 'result': outcome.result(),
                'termination': outcome.termination.name, 'fen': board.fen(),
                'moves': move_records, 'move_times': times,
                'avg_move_seconds': round(sum(times) / len(times), 4) if times else 0.0,
                'pgn': self._pgn(board, white['name'], black['name'], outcome.result())}

    @staticmethod
    def _pgn(board: chess.Board, white: str, black: str, result: str) -> str:
        import chess.pgn
        game = chess.pgn.Game.from_board(board)
        game.headers['Event'] = 'Local AI Chess Arena'
        game.headers['White'] = white
        game.headers['Black'] = black
        game.headers['Result'] = result
