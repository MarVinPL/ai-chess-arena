"""Validate the standalone gateway unit without installing it."""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / 'deploy/remote-worker/ai-chess-remote-worker.service.example'


def test_systemd_unit_validates_as_unit_file(tmp_path):
    if not shutil.which('systemd-analyze'):
        pytest.skip('systemd-analyze unavailable')
    unit = tmp_path / 'ai-chess-remote-worker.service'
    unit.write_text(TEMPLATE.read_text())
    result = subprocess.run(['systemd-analyze', 'verify', str(unit)],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_gateway_unit_is_isolated_from_main_arena():
    unit = TEMPLATE.read_text()
    assert 'ExecStart=' in unit and 'remote_worker_service.py' in unit
    assert 'Restart=on-failure' in unit
    assert 'MemoryMax=' in unit and 'TasksMax=' in unit
    assert 'PrivateTmp=true' in unit
    assert 'ProtectHome=read-only' in unit
    assert 'IPAddressDeny=any' in unit
    assert 'IPAddressAllow=localhost' in unit
    assert 'WantedBy=multi-user.target' in unit
    assert 'server.py' not in unit
