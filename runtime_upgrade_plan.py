"""Declarative, inert contract for a future isolated upstream llama.cpp runtime.

This module only builds an argv list and validates synthetic HTTP payloads. It
never starts a process, downloads a binary/model, changes the active runtime,
or authorizes model execution.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class RuntimeContractError(ValueError):
    """An upstream runtime response does not satisfy the runner contract."""


def validate_health_response(status_code: int, payload: Any) -> None:
    """Validate the documented llama-server health response without HTTP I/O."""
    if status_code != 200:
        raise RuntimeContractError(f"Runtime health returned HTTP {status_code}")
    if not isinstance(payload, Mapping) or payload.get("status") != "ok":
        raise RuntimeContractError("Runtime health response is not {status: 'ok'}")


def parse_chat_completion_response(
    status_code: int, payload: Any
) -> tuple[str, str | None, int | None]:
    """Extract the minimal non-streaming OpenAI-compatible runner response.

    Usage metadata is optional. If present but malformed, completion_tokens is
    treated as unavailable, matching the current runner's accounting behavior.
    """
    if status_code != 200:
        raise RuntimeContractError(f"Chat completion returned HTTP {status_code}")
    if not isinstance(payload, Mapping):
        raise RuntimeContractError("Chat completion response must be a JSON object")
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
        raise RuntimeContractError("Chat completion response has no choices[0] object")
    message = choices[0].get("message")
    if not isinstance(message, Mapping) or not isinstance(message.get("content"), str):
        raise RuntimeContractError("Chat completion response has no string message.content")
    finish_reason = choices[0].get("finish_reason")
    if finish_reason is not None and not isinstance(finish_reason, str):
        raise RuntimeContractError("Chat completion finish_reason must be a string or null")
    usage = payload.get("usage")
    completion_tokens = usage.get("completion_tokens") if isinstance(usage, Mapping) else None
    if isinstance(completion_tokens, bool) or not isinstance(completion_tokens, int) or completion_tokens < 0:
        completion_tokens = None
    return message["content"], finish_reason, completion_tokens


@dataclass(frozen=True)
class UpstreamLlamaServerProfile:
    """Candidate profile; intentionally not wired into the production runner."""

    binary: Path = Path(
        "/opt/ai-chess-arena/runtimes/llama.cpp-b10830/bin/llama-server"
    )
    host: str = "127.0.0.1"
    port: int = 8089
    threads: int = 2
    context: int = 2048
    parallel: int = 1

    def validate(self) -> None:
        if self.host != "127.0.0.1":
            raise ValueError("Candidate runtime must bind only to 127.0.0.1")
        if int(self.port) != 8089:
            raise ValueError("Candidate runtime must use isolated port 8089")
        if int(self.threads) < 1 or int(self.context) < 512 or int(self.parallel) != 1:
            raise ValueError("Invalid candidate runtime resource limits")

    @property
    def api_base(self) -> str:
        self.validate()
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"http://{host}:{int(self.port)}"

    def build_argv(self, model_path: str | Path) -> list[str]:
        """Return a deterministic command; caller must not execute it implicitly."""
        self.validate()
        model = Path(model_path).resolve(strict=False)
        if model.suffix.lower() != ".gguf":
            raise ValueError("Candidate runtime accepts GGUF paths only")
        return [
            str(self.binary),
            "-m", str(model),
            "--host", self.host,
            "--port", str(int(self.port)),
            "-t", str(int(self.threads)),
            "-c", str(int(self.context)),
            "-np", "1",
            "-ngl", "0",
            "--offline",
            "--no-webui",
            "--no-warmup",
        ]
