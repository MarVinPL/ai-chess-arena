"""Opt-in, isolated upstream llama-server adapter.

This adapter is never selected by default. Tests use mocked process and HTTP
objects; importing this module does not start a process or contact the network.
"""
from __future__ import annotations

import json
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Callable

from engine_runner import LlamafileRuntime, RunnerError
from runtime_upgrade_plan import (
    RuntimeContractError,
    UpstreamLlamaServerProfile,
    parse_chat_completion_response,
    validate_health_response,
)


class _ReplayResponse:
    """Small context-manager response that replays bytes already validated."""

    def __init__(self, status: int, body: bytes):
        self.status = status
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


class UpstreamLlamaServerRuntime(LlamafileRuntime):
    """Owns only its own upstream llama-server child on loopback port 8089."""

    RUNTIME_NAME = "llama.cpp-upstream"

    def __init__(
        self,
        profile: UpstreamLlamaServerProfile | None = None,
        startup_timeout: float = 90,
        popen: Callable = subprocess.Popen,
        urlopen: Callable | None = None,
        runtime_root: Path = Path("/opt/ai-chess-arena/runtimes"),
        model_root: Path | None = None,
        on_status: Callable | None = None,
        monitor_interval: float = 30,
        sleep: Callable = time.sleep,
        monotonic: Callable = time.monotonic,
    ):
        self.profile = profile or UpstreamLlamaServerProfile()
        self.profile.validate()
        if self.profile.host != "127.0.0.1" or self.profile.port != 8089:
            raise ValueError("Upstream runtime must use 127.0.0.1:8089")
        self._http_open = urlopen
        self.runtime_root = Path(runtime_root).resolve(strict=False)
        if model_root is None:
            from model_store import MANAGED_DIR
            model_root = MANAGED_DIR
        self.model_root = Path(model_root).resolve(strict=False)
        binary = Path(self.profile.binary).resolve(strict=False)
        if not binary.is_relative_to(self.runtime_root):
            raise ValueError("Upstream llama-server binary must stay inside the dedicated runtime directory")
        self._sleep = sleep
        self._monotonic = monotonic
        super().__init__(
            binary=self.profile.binary,
            api_base=self.profile.api_base,
            threads=self.profile.threads,
            context=self.profile.context,
            startup_timeout=startup_timeout,
            popen=popen,
            on_status=on_status,
            monitor_interval=monitor_interval,
        )
        # The inherited chess move generator now validates every upstream chat
        # response before its existing parser can consume the replayed payload.
        self._urlopen = self._validated_urlopen

    def _validated_urlopen(self, request, timeout=None):
        url = request.full_url if hasattr(request, "full_url") else str(request)
        response = (self._http_open or urllib.request.urlopen)(request, timeout=timeout)
        status = getattr(response, "status", getattr(response, "code", 200))
        try:
            body = response.read()
            if url.endswith("/v1/chat/completions"):
                parse_chat_completion_response(status, json.loads(body.decode("utf-8")))
            elif url.endswith("/health"):
                validate_health_response(status, json.loads(body.decode("utf-8")))
            return _ReplayResponse(status, body)
        finally:
            close = getattr(response, "close", None)
            if close:
                close()

    def _healthy(self) -> bool:
        request = urllib.request.Request(self.api_base + "/health")
        try:
            with (self._http_open or urllib.request.urlopen)(request, timeout=1.5) as response:
                validate_health_response(
                    response.status, json.loads(response.read().decode("utf-8"))
                )
            return True
        except Exception:
            return False

    def ensure_loaded(self, model_path: str | Path, draft_model_path: str | Path | None = None) -> None:
        if draft_model_path is not None:
            raise RunnerError("Upstream llama-server adapter nie obsługuje draft model")
        try:
            model = Path(model_path).resolve(strict=True)
        except OSError as exc:
            raise RunnerError(f"Nie znaleziono pliku GGUF: {model_path}") from exc
        if not model.is_file() or model.suffix.lower() != ".gguf":
            raise RunnerError(f"Nieprawidłowy plik modelu GGUF: {model}")
        if not model.is_relative_to(self.model_root):
            raise RunnerError("Model upstream musi pochodzić wyłącznie z arena-managed model store")
        try:
            binary = Path(self.profile.binary).resolve(strict=True)
        except OSError as exc:
            raise RunnerError(f"Nie znaleziono binarium llama-server: {self.profile.binary}") from exc
        if not binary.is_file() or not binary.stat().st_mode & 0o111:
            raise RunnerError(f"Brak wykonywalnego llama-server: {binary}")
        if not binary.is_relative_to(self.runtime_root):
            raise RunnerError("Binarium upstream znajduje się poza dedykowanym katalogiem runtime")

        with self._lock:
            if self.loaded_model == str(model) and self._healthy():
                return
            self.stop()
            self._notify("model_loading", f"Ładowanie modelu upstream: {model.name}", model=model.name)
            args = self.profile.build_argv(model)
            self._process = self._popen(
                args,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            self._model_path = model
            self._draft_model_path = None
            self._desired_model_path = model
            self._desired_draft_model_path = None
            deadline = self._monotonic() + self.startup_timeout
            while self._monotonic() < deadline:
                if self._process.poll() is not None:
                    code = self._process.returncode
                    self._process = None
                    self._model_path = None
                    self._desired_model_path = None
                    raise RunnerError(f"llama-server zakończył start kodem {code}")
                if self._healthy():
                    self._notify("model_ready", f"Model upstream gotowy: {model.name}", model=model.name)
                    return
                self._sleep(0.5)
            self.stop()
            raise RunnerError("Przekroczono czas oczekiwania na gotowość upstream llama-server")

    def generate(self, *args, **kwargs):
        if kwargs.get("draft_model_path") is not None:
            raise RunnerError("Upstream llama-server adapter nie obsługuje draft model")
        move, metadata = super().generate(*args, **kwargs)
        metadata["runtime"] = self.RUNTIME_NAME
        return move, metadata
