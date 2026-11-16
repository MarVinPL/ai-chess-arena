"""Explicit runtime selection for the arena server.

The default is always the existing llamafile runtime. Upstream llama-server
requires two independent opt-ins and never serves as an automatic fallback.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Callable


def create_runtime(
    *,
    on_status: Callable | None = None,
    environ: dict[str, str] | None = None,
    popen=None,
    urlopen=None,
    model_root: Path | None = None,
):
    """Construct a runtime from explicit configuration; default to llamafile."""
    env = os.environ if environ is None else environ
    selected = env.get("ARENA_RUNTIME", "llamafile").strip().lower()

    if selected == "llamafile":
        from engine_runner import LlamafileRuntime

        kwargs = {"on_status": on_status}
        if popen is not None:
            kwargs["popen"] = popen
        return LlamafileRuntime(**kwargs)

    if selected != "llama-server":
        raise ValueError(f"Nieznany ARENA_RUNTIME: {selected!r}")

    if env.get("ARENA_UPSTREAM_RUNTIME_ENABLED") != "1":
        raise ValueError(
            "Upstream runtime jest domyślnie wyłączony; wymagane "
            "ARENA_UPSTREAM_RUNTIME_ENABLED=1"
        )

    from runtime_upgrade_plan import UpstreamLlamaServerProfile
    from upstream_llama_runtime import UpstreamLlamaServerRuntime

    binary = Path(env.get(
        "ARENA_UPSTREAM_LLAMA_SERVER_BIN",
        "/opt/ai-chess-arena/runtimes/llama.cpp-b10830/bin/llama-server",
    ))
    runtime_root = Path(env.get(
        "ARENA_UPSTREAM_RUNTIME_ROOT", "/opt/ai-chess-arena/runtimes"
    ))
    kwargs = {
        "profile": UpstreamLlamaServerProfile(binary=binary),
        "runtime_root": runtime_root,
        "on_status": on_status,
    }
    if model_root is not None:
        kwargs["model_root"] = model_root
    if popen is not None:
        kwargs["popen"] = popen
    if urlopen is not None:
        kwargs["urlopen"] = urlopen
    return UpstreamLlamaServerRuntime(**kwargs)
