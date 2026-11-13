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
    pass


class LlamafileRuntime:
    """Owns exactly one llamafile child process; never kills unrelated processes."""

    def __init__(self, binary: Path = LLAMAFILE, api_base: str = API_BASE,
                 threads: int = 2, context: int = 2048, startup_timeout: float = 90,
                 popen: Callable = subprocess.Popen, on_status: Callable | None = None):
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

    def stop(self) -> None:
        with self._lock:
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
            deadline = time.monotonic() + self.startup_timeout
            while time.monotonic() < deadline:
                if self._process.poll() is not None:
                    code = self._process.returncode
                    self._process = None; self._model_path = None; self._draft_model_path = None
                    raise RunnerError(f'llamafile zakończył start kodem {code}')
                if self._healthy():
                    self._notify('model_ready', f'Model gotowy: {path.name}', model=path.name)
                    return
                time.sleep(0.5)
            self.stop()
            raise RunnerError('Przekroczono czas oczekiwania na gotowość llamafile (to nie jest limit czasu ruchu)')

    def generate(self, model_path: str | Path, board: chess.Board,
                 stop_event: threading.Event | None = None,
                 draft_model_path: str | Path | None = None) -> tuple[chess.Move, dict]:
        self.ensure_loaded(model_path, draft_model_path)
        if stop_event and stop_event.is_set():
            raise RunnerError('Turniej zatrzymany przez operatora')
        legal = [move.uci() for move in board.legal_moves]
        if not legal:
            raise RunnerError('Pozycja nie zawiera legalnych ruchów')
        color = 'white' if board.turn == chess.WHITE else 'black'
        legal_text = ", ".join(legal)
        initial_prompt = (
            'Jesteś graczem szachowym. Wybierz dokładnie jeden ruch z listy LEGAL MOVES. '
            'Zwróć wyłącznie ruch UCI.\n'
            f'FEN: {board.fen()}\nLEGAL MOVES: {legal_text}\nMOVE:')
        retry_prompt = None
        self._notify('generating', 'Model generuje propozycję ruchu', model=Path(model_path).name, color=color, fen=board.fen(), legal_moves=legal, response=None)
        started = time.monotonic()
        content = ''
        move = None
        # One bounded retry handles malformed model output without inventing a move.
        # No per-move timeout: user-requested timing is measured, not forfeited.
        for attempt in range(2):
            if attempt and stop_event and stop_event.is_set():
                raise RunnerError('Turniej zatrzymany przez operatora')
            prompt = initial_prompt if attempt == 0 else retry_prompt
            payload = json.dumps({'messages': [{'role': 'user', 'content': prompt}],
                                  'temperature': 0, 'max_tokens': 16, 'stream': False,
                                  'chat_template_kwargs': {'enable_thinking': False}}).encode()
            request = urllib.request.Request(self.api_base + '/v1/chat/completions', data=payload,
                                             headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(request, timeout=None) as response:
                data = json.loads(response.read().decode())
            try:
                content = data['choices'][0]['message'].get('content', '')
            except (KeyError, IndexError, TypeError) as exc:
                raise RunnerError('Nieprawidłowa odpowiedź API llamafile') from exc
            if not isinstance(content, str):
                content = str(content or '')
            match = re.search(r'(?<![a-h1-8])([a-h][1-8][a-h][1-8][qrbn]?)(?![a-h1-8])', content.lower())
            illegal_uci = None
            if match:
                candidate = chess.Move.from_uci(match.group(1))
                if candidate in board.legal_moves:
                    move = candidate
                    break
                illegal_uci = candidate.uci()
            if attempt == 0:
                if illegal_uci:
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
            raise RunnerError(f'Model nie zwrócił legalnego ruchu UCI po 2 próbach: {content[:120]!r}')
        self._notify('move_generated', f'Model zaproponował ruch: {move.uci()}', model=Path(model_path).name, color=color, fen=board.fen(), legal_moves=legal, response=content[:300], proposed_move=move.uci(), elapsed_seconds=round(elapsed,4))
        return move, {'seconds': round(elapsed, 4), 'runtime': 'llamafile',
                      'model_path': str(Path(model_path).resolve()),
                      'draft_model_path': str(Path(draft_model_path).resolve()) if draft_model_path else None,
                      'response': content[:160]}


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
            if player.get('draft_path'):
                move, meta = self.runtime.generate(player['path'], board, stop_event, draft_model_path=player['draft_path'])
            else:
                move, meta = self.runtime.generate(player['path'], board, stop_event)
            san = board.san(move)
            board.push(move)
            record = {'ply': len(move_records) + 1, 'color': color,
                      'model': player['name'], 'uci': move.uci(), 'san': san,
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
        return str(game)
