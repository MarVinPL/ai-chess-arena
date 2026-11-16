"""Declarative, inert contract for a future isolated upstream llama.cpp runtime.

This module only builds an argv list. It never starts a process, downloads a
binary/model, changes the active runtime, or authorizes model execution.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


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
        if self.host not in {"127.0.0.1", "::1", "localhost"}:
            raise ValueError("Candidate runtime must bind to loopback only")
        if not 1 <= int(self.port) <= 65535:
            raise ValueError("Invalid candidate runtime port")
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
            "--no-webui",
            "--no-warmup",
        ]
