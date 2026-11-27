"""Safe process-level API settings for isolated integration runs."""
from __future__ import annotations

import ipaddress
import os
from pathlib import Path
from typing import Mapping


class RuntimeProcessConfigError(ValueError):
    """Raised when an API process setting is unsafe or malformed."""


def resolve_process_settings(
    environ: Mapping[str, str] | None = None,
    *,
    root: Path,
) -> tuple[Path, str, int]:
    """Resolve database and HTTP bind settings without touching the filesystem.

    HTTP is deliberately loopback-only. Port zero is supported for tests that
    let the OS allocate an unused ephemeral port. Defaults preserve production.
    """
    env = os.environ if environ is None else environ
    raw_db = env.get("ARENA_DATABASE_PATH", str(root / "arena.sqlite3")).strip()
    if not raw_db or "\x00" in raw_db:
        raise RuntimeProcessConfigError("ARENA_DATABASE_PATH must be a non-empty path.")
    database = Path(raw_db).expanduser()
    if not database.is_absolute() or ".." in database.parts:
        raise RuntimeProcessConfigError("ARENA_DATABASE_PATH must be an absolute canonical path.")

    host = env.get("ARENA_HTTP_HOST", "127.0.0.1").strip()
    if host.lower() == "localhost":
        host = "127.0.0.1"
    try:
        if not ipaddress.ip_address(host).is_loopback:
            raise RuntimeProcessConfigError("ARENA_HTTP_HOST must be a loopback address.")
    except ValueError as exc:
        if isinstance(exc, RuntimeProcessConfigError):
            raise
        raise RuntimeProcessConfigError("ARENA_HTTP_HOST must be a loopback address.") from exc

    raw_port = env.get("ARENA_HTTP_PORT", "8090").strip()
    try:
        port = int(raw_port)
    except (TypeError, ValueError) as exc:
        raise RuntimeProcessConfigError("ARENA_HTTP_PORT must be an integer from 0 to 65535.") from exc
    if not 0 <= port <= 65535:
        raise RuntimeProcessConfigError("ARENA_HTTP_PORT must be an integer from 0 to 65535.")
    return database, host, port


def resolve_log_directory(
    environ: Mapping[str, str] | None = None,
    *,
    root: Path,
) -> Path:
    """Resolve the API log directory, defaulting to the historical app path."""
    env = os.environ if environ is None else environ
    raw = env.get("ARENA_LOG_DIR", str(root / "logs")).strip()
    if not raw or "\x00" in raw:
        raise RuntimeProcessConfigError("ARENA_LOG_DIR must be a non-empty path.")
    path = Path(raw).expanduser()
    if not path.is_absolute() or ".." in path.parts:
        raise RuntimeProcessConfigError("ARENA_LOG_DIR must be an absolute canonical path.")
    return path


def background_jobs_enabled(environ: Mapping[str, str] | None = None) -> bool:
    """Return whether optional catalog/planner background loops should start."""
    env = os.environ if environ is None else environ
    value = env.get("ARENA_BACKGROUND_JOBS", "1").strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise RuntimeProcessConfigError(
        "ARENA_BACKGROUND_JOBS must be one of 1/0, true/false, yes/no, on/off."
    )
