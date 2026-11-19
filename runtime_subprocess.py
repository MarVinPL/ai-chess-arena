"""Hard-bounded, process-group-owned GGUF runtime smoke-test adapter.

The adapter is safe to construct but is not wired as a default runner. A caller
must pass it explicitly to runtime_executor.execute_job after both execution
gates and a per-batch approval have been satisfied.
"""
from __future__ import annotations

import hashlib
import json
import os
import queue
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

import hf_candidate_downloads

WORKER = Path(__file__).with_name('runtime_worker.py').resolve()
HEALTH_PATH = '/health'
INFERENCE_PATH = '/v1/chat/completions'


class RuntimeProcessError(RuntimeError):
    pass


def _reserve_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(('127.0.0.1', 0))
        return int(listener.getsockname()[1])


def _process_start_ticks(pid: int) -> tuple[int, int, int]:
    raw = Path(f'/proc/{pid}/stat').read_text()
    close = raw.rfind(')')
    if close < 0:
        raise RuntimeProcessError('Nie można odczytać tożsamości procesu runtime.')
    fields = raw[close + 2:].split()
    pgrp, session, start_ticks = int(fields[2]), int(fields[3]), int(fields[19])
    if pgrp != pid or session != pid:
        raise RuntimeProcessError('Proces runtime nie jest liderem odizolowanej grupy.')
    return pgrp, session, start_ticks


def _tail(path: Path, limit: int = 4000) -> str:
    try:
        with path.open('rb') as stream:
            stream.seek(0, os.SEEK_END)
            size = stream.tell()
            stream.seek(max(0, size - limit), os.SEEK_SET)
            return stream.read(limit).decode('utf-8', 'replace')
    except OSError:
        return ''


def _stop_process_group(process, *, wait_seconds: float = 3.0,
                        monotonic: Callable = time.monotonic,
                        sleep: Callable = time.sleep,
                        killpg: Callable = os.killpg) -> None:
    """Terminate the dedicated session/process group, escalating to SIGKILL."""
    try:
        killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    deadline = monotonic() + wait_seconds
    while monotonic() < deadline:
        # poll() reaps an exited group leader; killpg(0) then still detects any
        # surviving descendants that inherited its dedicated process group.
        process.poll()
        try:
            killpg(process.pid, 0)
            group_exists = True
        except ProcessLookupError:
            group_exists = False
        if not group_exists:
            break
        sleep(0.1)
    try:
        killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=wait_seconds)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=wait_seconds)


class LlamafileSmokeRunner:
    """Load one quarantined GGUF and make one bounded, fixed smoke inference."""

    def __init__(self, *, popen: Callable = subprocess.Popen,
                 urlopen: Callable = urllib.request.urlopen,
                 monotonic: Callable = time.monotonic,
                 sleep: Callable = time.sleep,
                 temp_root: Path | None = None,
                 terminate: Callable = _stop_process_group,
                 process_identity: Callable = _process_start_ticks):
        self._popen = popen
        self._urlopen = urlopen
        self._monotonic = monotonic
        self._sleep = sleep
        self._temp_root = Path(temp_root) if temp_root else None
        self._terminate = terminate
        self._process_identity = process_identity

    @staticmethod
    def _verified_model(payload: dict) -> Path:
        path = Path(str(payload.get('path') or '')).resolve(strict=True)
        quarantine = Path(hf_candidate_downloads.STAGING_DIR).resolve(strict=True)
        if not path.is_file() or not path.is_relative_to(quarantine):
            raise PermissionError('Model musi pozostać zwykłym plikiem wewnątrz kwarantanny.')
        expected = str(payload.get('sha256') or '').lower()
        if len(expected) != 64 or any(c not in '0123456789abcdef' for c in expected):
            raise PermissionError('Brak poprawnego SHA-256 przypiętego artefaktu.')
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(block)
        if digest.hexdigest() != expected:
            raise PermissionError('SHA-256 pliku kwarantanny nie zgadza się z zatwierdzonym artefaktem.')
        return path

    def __call__(self, payload: dict, timeout_seconds: int,
                 cancellation_requested: Callable[[], bool]) -> dict:
        if not isinstance(payload, dict):
            raise TypeError('Nieprawidłowy payload testu runtime.')
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int) or not 1 <= timeout_seconds <= 1800:
            raise ValueError('Timeout testu musi mieścić się w zakresie 1–1800 sekund.')
        if not callable(cancellation_requested):
            raise TypeError('Brak callbacku anulowania testu.')
        model = self._verified_model(payload)
        port = _reserve_loopback_port()
        base = f'http://127.0.0.1:{port}'
        started = self._monotonic()
        deadline = started + timeout_seconds

        with tempfile.TemporaryDirectory(prefix='arena-runtime-', dir=self._temp_root) as temp_dir:
            output_dir = Path(temp_dir)
            log_path = output_dir / 'runtime.log'
            process = None
            ready_at = None
            inference_result: queue.Queue = queue.Queue(maxsize=1)
            try:
                with log_path.open('wb') as log_file:
                    command = [sys.executable, str(WORKER), str(model),
                               str(payload['sha256']), str(port)]
                    process = self._popen(
                        command, stdin=subprocess.DEVNULL, stdout=log_file,
                        stderr=subprocess.STDOUT, cwd=str(output_dir),
                        env={'PATH': '/usr/bin:/bin', 'HOME': str(output_dir),
                             'TMPDIR': str(output_dir), 'LANG': 'C.UTF-8'},
                        close_fds=True, start_new_session=True)
                    pgrp, session, start_ticks = self._process_identity(process.pid)
                    register_process = payload.get('register_process')
                    if callable(register_process):
                        register_process(process.pid, pgrp, start_ticks)
                    while self._monotonic() < deadline:
                        if cancellation_requested():
                            raise InterruptedError('Test runtime anulowano lub cofnięto zgodę.')
                        exit_code = process.poll()
                        if exit_code is not None:
                            raise RuntimeProcessError(
                                f'Proces runtime zakończył się kodem {exit_code}. {_tail(log_path)}')
                        try:
                            request = urllib.request.Request(base + HEALTH_PATH)
                            with self._urlopen(request, timeout=min(0.5, max(0.05, deadline - self._monotonic()))) as response:
                                if response.status == 200:
                                    ready_at = self._monotonic()
                                    break
                        except (OSError, urllib.error.URLError, TimeoutError):
                            pass
                        self._sleep(0.2)
                    if ready_at is None:
                        if self._monotonic() >= deadline:
                            raise TimeoutError('Przekroczono twardy timeout uruchamiania modelu.')
                        raise RuntimeProcessError('Runtime nie osiągnął stanu healthy.')

                    body = json.dumps({
                        'messages': [{'role': 'user', 'content': 'Reply with exactly READY.'}],
                        'temperature': 0, 'max_tokens': 8, 'stream': False,
                    }).encode('utf-8')
                    request = urllib.request.Request(
                        base + INFERENCE_PATH, data=body,
                        headers={'Content-Type': 'application/json'}, method='POST')

                    def infer():
                        try:
                            remaining = max(0.1, deadline - self._monotonic())
                            with self._urlopen(request, timeout=remaining) as response:
                                inference_result.put((True, json.loads(response.read().decode('utf-8'))))
                        except Exception as exc:
                            inference_result.put((False, exc))

                    inference_thread = threading.Thread(target=infer, name='arena-runtime-smoke-inference', daemon=True)
                    inference_thread.start()
                    while inference_thread.is_alive():
                        if cancellation_requested():
                            raise InterruptedError('Test runtime anulowano podczas inferencji.')
                        if process.poll() is not None:
                            raise RuntimeProcessError('Proces runtime zakończył się podczas inferencji.')
                        if self._monotonic() >= deadline:
                            raise TimeoutError('Przekroczono twardy timeout inferencji.')
                        self._sleep(0.1)
                    ok, value = inference_result.get_nowait()
                    if not ok:
                        raise RuntimeProcessError(f'Błąd zapytania inferencyjnego: {value}')
                    try:
                        content = value['choices'][0]['message']['content']
                    except (KeyError, IndexError, TypeError) as exc:
                        raise RuntimeProcessError('Nieprawidłowa odpowiedź API modelu.') from exc
                    if not isinstance(content, str) or not content.strip():
                        raise RuntimeProcessError('Model zwrócił pustą odpowiedź w teście smoke.')
                    finished = self._monotonic()
                    return {
                        'ok': True, 'runtime': 'llamafile', 'test': 'load_and_single_inference',
                        'startup_seconds': round(ready_at - started, 3),
                        'inference_seconds': round(finished - ready_at, 3),
                        'total_seconds': round(finished - started, 3),
                        'response_excerpt': content[:300],
                        'model_sha256': payload['sha256'],
                        'process_group_isolated': True,
                        'output_isolated': True,
                    }
            finally:
                if process is not None:
                    self._terminate(process)
