from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UNIT = ROOT / "deploy" / "systemd" / "ai-chess-arena.service"
RUNBOOK = ROOT / "docs" / "deployment" / "arena-supervisor-handoff-2026-11-14.md"


def test_dedicated_unit_is_independent_and_uses_existing_runtime():
    unit = UNIT.read_text(encoding="utf-8")
    assert "User=debian" in unit
    assert "Group=debian" in unit
    assert "WorkingDirectory=/home/debian/ai-chess-arena" in unit
    assert "ExecStart=/home/debian/ai-chess-arena/.venv/bin/python /home/debian/ai-chess-arena/server.py" in unit
    assert "KillMode=control-group" in unit
    assert "sentinelx-cloud-core.service" not in unit
    assert "/home/debian/ai-agent/models" not in unit


def test_handoff_guards_exact_process_and_preserves_tournament_state():
    runbook = RUNBOOK.read_text(encoding="utf-8")
    assert "tournament_running=false" in runbook
    assert "PRAGMA integrity_check" in runbook
    assert "/proc/<PID>/cmdline" in runbook
    assert "os.kill(pid, signal.SIGTERM)" in runbook
    assert "Never use pkill" in runbook
    assert "Never restart, stop, reload, or signal sentinelx-cloud-core.service." in runbook
    assert "systemctl start ai-chess-arena.service" in runbook
    assert "does not start, resume, reset, or alter tournaments #4 or #5" in runbook
