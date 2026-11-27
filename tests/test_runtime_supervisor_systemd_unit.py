from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _unit_text():
    return (ROOT / "deploy/systemd/ai-chess-arena-runtime-supervisor.service").read_text()


def test_supervisor_unit_uses_notify_readiness_and_pinned_isolated_bundle():
    unit = _unit_text()
    assert "Type=notify" in unit
    assert "NotifyAccess=main" in unit
    assert "ExecStart=/usr/bin/python3 -I -S /usr/local/lib/ai-chess-arena-runtime/runtime_supervisor.pyz" in unit
    assert "EnvironmentFile=/etc/ai-chess-arena/runtime-supervisor.env" in unit
    assert "User=root" in unit
    assert "Group=debian" in unit
    assert "RuntimeDirectory=ai-chess-arena-runtime" in unit
    assert "RuntimeDirectoryMode=0750" in unit
    assert "Before=ai-chess-arena.service" in unit


def test_supervisor_unit_has_narrow_host_access_and_no_ip_network():
    unit = _unit_text()
    assert "ProtectSystem=strict" in unit
    assert "ProtectHome=read-only" in unit
    assert "RestrictAddressFamilies=AF_UNIX" in unit
    assert "CapabilityBoundingSet=CAP_CHOWN CAP_DAC_READ_SEARCH" in unit
    assert "ReadWritePaths=/var/lib/arena-handoff /run/arena-runtime" in unit
    assert "PrivateNetwork=true" not in unit
    assert "ARENA_RUNTIME_EXECUTION_ENABLED=1" not in unit


def test_api_dependency_is_a_production_only_dropin_example():
    example = ROOT / "deploy/systemd/ai-chess-arena.service.d/20-runtime-supervisor.conf.example"
    text = example.read_text()
    assert "Requires=ai-chess-arena-runtime-supervisor.service" in text
    assert "After=ai-chess-arena-runtime-supervisor.service" in text
    # The existing API unit remains deployable independently in disabled/staging mode.
    api_unit = (ROOT / "deploy/systemd/ai-chess-arena.service").read_text()
    assert "ai-chess-arena-runtime-supervisor.service" not in api_unit
