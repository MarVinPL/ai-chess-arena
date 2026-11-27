from pathlib import Path

import pytest

from runtime_process_config import (
    RuntimeProcessConfigError, background_jobs_enabled, resolve_log_directory,
    resolve_process_settings,
)


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


def test_log_directory_is_configurable_and_defaults_to_application_logs(tmp_path):
    assert resolve_log_directory({}, root=tmp_path) == tmp_path / "logs"
    isolated = tmp_path / "test-logs"
    assert resolve_log_directory({"ARENA_LOG_DIR": str(isolated)}, root=tmp_path) == isolated


@pytest.mark.parametrize("value", ["", "relative", "/tmp/../logs"])
def test_log_directory_rejects_unsafe_paths(value, tmp_path):
    with pytest.raises(RuntimeProcessConfigError, match="ARENA_LOG_DIR"):
        resolve_log_directory({"ARENA_LOG_DIR": value}, root=tmp_path)


def test_background_jobs_default_on_and_can_be_disabled_for_isolated_tests():
    assert background_jobs_enabled({}) is True
    assert background_jobs_enabled({"ARENA_BACKGROUND_JOBS": "0"}) is False
    assert background_jobs_enabled({"ARENA_BACKGROUND_JOBS": "false"}) is False
    with pytest.raises(RuntimeProcessConfigError, match="ARENA_BACKGROUND_JOBS"):
        background_jobs_enabled({"ARENA_BACKGROUND_JOBS": "sometimes"})


def test_api_process_starts_serves_and_shuts_down_with_isolated_parameters(tmp_path):
    import os
    import select
    import signal
    import subprocess
    import sys
    import time
    from urllib.request import urlopen

    database = tmp_path / "state" / "isolated.sqlite3"
    logs = tmp_path / "logs"
    env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONPATH=".",
               ARENA_DATABASE_PATH=str(database), ARENA_LOG_DIR=str(logs),
               ARENA_HTTP_HOST="127.0.0.1", ARENA_HTTP_PORT="0",
               ARENA_BACKGROUND_JOBS="0", ARENA_RUNTIME_DEPLOYMENT="disabled")
    process = subprocess.Popen(
        [sys.executable, "server.py"], stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, env=env,
    )
    try:
        deadline = time.monotonic() + 20
        address = None
        output = []
        while time.monotonic() < deadline:
            ready, _, _ = select.select([process.stdout], [], [], 0.25)
            if ready:
                line = process.stdout.readline()
                if line:
                    output.append(line)
                    if "Local AI Chess Arena: http://" in line:
                        address = line.strip().split("Local AI Chess Arena: ", 1)[1]
                        break
            if process.poll() is not None:
                break
        assert address, "API did not bind; output: " + "".join(output)
        with urlopen(address + "/", timeout=5) as response:
            assert response.status == 200
            assert response.read(128)
        process.send_signal(signal.SIGTERM)
        assert process.wait(timeout=10) == 0
        assert database.is_file()
        assert logs.is_dir()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        if process.stdout:
            process.stdout.close()
