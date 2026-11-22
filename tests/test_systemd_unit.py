from pathlib import Path
import shutil
import subprocess

UNIT = Path(__file__).resolve().parents[1] / "deploy" / "systemd" / "ai-chess-arena.service"


def test_arena_service_has_independent_identity_and_lifecycle():
    text = UNIT.read_text(encoding="utf-8")
    assert "User=debian" in text
    assert "Group=debian" in text
    assert "WorkingDirectory=/home/debian/ai-chess-arena" in text
    assert "ExecStart=/home/debian/ai-chess-arena/.venv/bin/python /home/debian/ai-chess-arena/server.py" in text
    assert "Restart=on-failure" in text
    assert "KillMode=control-group" in text
    assert "TimeoutStopSec=30s" in text
    assert "NoNewPrivileges=true" in text
    assert "ProtectHome=read-only" in text
    assert "BindPaths=/home/debian/ai-chess-arena" in text
    assert "ReadWritePaths=/home/debian/ai-chess-arena" in text
    assert "ReadOnlyPaths=/home/debian/ai-agent/bin/llamafile" in text
    assert "InaccessiblePaths=/home/debian/ai-agent/models" in text
    assert "sentinelx-cloud-core.service" not in text


def test_arena_service_uses_no_shell_or_sentinelx_restart_commands():
    text = UNIT.read_text(encoding="utf-8")
    assert "ExecStart=/bin/bash" not in text
    assert "systemctl restart" not in text
    assert "systemctl stop" not in text
    assert "Requires=sentinelx-cloud-core.service" not in text
    assert "After=sentinelx-cloud-core.service" not in text


def test_systemd_analyze_verifies_unit_when_available():
    binary = shutil.which("systemd-analyze")
    if not binary:
        import pytest
        pytest.skip("systemd-analyze is not installed")
    # The unit references the production VPS virtualenv by absolute path.
    # A hosted CI runner does not have that deployment path, so verification
    # is meaningful only where the configured executable is present.
    configured_python = Path("/home/debian/ai-chess-arena/.venv/bin/python")
    if not configured_python.exists():
        import pytest
        pytest.skip("production virtualenv is not present on this host")
    result = subprocess.run(
        [binary, "verify", str(UNIT)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
