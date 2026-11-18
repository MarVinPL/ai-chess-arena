from pathlib import Path

import pytest

from runtime_process_config import RuntimeProcessConfigError, resolve_process_settings


def test_defaults_preserve_existing_production_bind_and_database(tmp_path):
    assert resolve_process_settings({}, root=tmp_path) == (
        tmp_path / "arena.sqlite3", "127.0.0.1", 8090
    )


def test_isolated_database_and_ephemeral_loopback_port_are_configurable(tmp_path):
    database = tmp_path / "isolated" / "integration.sqlite3"
    assert resolve_process_settings({
        "ARENA_DATABASE_PATH": str(database),
        "ARENA_HTTP_HOST": "localhost",
        "ARENA_HTTP_PORT": "0",
    }, root=Path("/unused")) == (database, "127.0.0.1", 0)


@pytest.mark.parametrize("value", ["", "relative.sqlite3", "/tmp/../arena.sqlite3"])
def test_database_path_rejects_empty_relative_and_parent_traversal(value, tmp_path):
    with pytest.raises(RuntimeProcessConfigError, match="ARENA_DATABASE_PATH"):
        resolve_process_settings({"ARENA_DATABASE_PATH": value}, root=tmp_path)


@pytest.mark.parametrize("host", ["0.0.0.0", "192.0.2.1", "example.com"])
def test_http_bind_rejects_non_loopback_hosts(host, tmp_path):
    with pytest.raises(RuntimeProcessConfigError, match="loopback"):
        resolve_process_settings({"ARENA_HTTP_HOST": host}, root=tmp_path)


@pytest.mark.parametrize("port", ["-1", "65536", "http", ""])
def test_http_port_rejects_invalid_values(port, tmp_path):
    with pytest.raises(RuntimeProcessConfigError, match="ARENA_HTTP_PORT"):
        resolve_process_settings({"ARENA_HTTP_PORT": port}, root=tmp_path)


def test_server_and_sqlite_subsystems_share_configured_database(tmp_path):
    import os
    import subprocess
    import sys

    database = tmp_path / "isolated.sqlite3"
    script = (
        "import server; from pathlib import Path; "
        f"expected=Path({str(database)!r}); "
        "assert server.DB == expected; "
        "assert server.league.DB == expected; "
        "assert server.hf_candidate_downloads.DB_PATH == expected; "
        "assert server.HOST == '127.0.0.1'; assert server.PORT == 0"
    )
    env = dict(os.environ, PYTHONPATH=".", ARENA_DATABASE_PATH=str(database),
               ARENA_HTTP_HOST="127.0.0.1", ARENA_HTTP_PORT="0")
    subprocess.run([sys.executable, "-c", script], check=True, env=env)
