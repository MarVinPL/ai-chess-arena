"""Validate the standalone gateway unit without installing it."""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / 'deploy/remote-worker/ai-chess-remote-worker.service.example'


def test_systemd_unit_validates_as_unit_file(tmp_path):
    if not shutil.which('systemd-analyze'):
        pytest.skip('systemd-analyze unavailable')
    unit = tmp_path / 'ai-chess-remote-worker.service'
    template = TEMPLATE.read_text()
    # The deployment template points to the VPS virtualenv; GitHub runners
    # cannot resolve that path. Validate a disposable copy with the runner's
    # Python and checkout location, without changing deployment semantics.
    vps_root = '/home/debian/ai-chess-arena'
    vps_exec = f'{vps_root}/.venv/bin/python {vps_root}/remote_worker_service.py'
    assert f'ExecStart={vps_exec}' in template
    portable = template.replace(
        f'ExecStart={vps_exec}',
        f'ExecStart={sys.executable} {ROOT / "remote_worker_service.py"}',
    )
    portable = portable.replace(
        f'WorkingDirectory={vps_root}', f'WorkingDirectory={ROOT}',
    )
    portable = portable.replace(
        f'ReadWritePaths={vps_root}', f'ReadWritePaths={tmp_path}',
    )
    unit.write_text(portable)
    result = subprocess.run(['systemd-analyze', 'verify', str(unit)],
                            capture_output=True, text=True, timeout=10)
    # Newer runner images print systemd warnings for *other installed units*
    # (e.g. xfs_scrub_all.service with deprecated CPUAccounting).
    # Only diagnostics of our temporary gateway unit indicate a bad template.
    diagnostics = [line for line in result.stderr.splitlines()
                   if unit.name in line or str(unit) in line]
    assert not diagnostics, '\n'.join(diagnostics)
    assert result.returncode == 0 or result.stderr.strip(), 'systemd-analyze failed without diagnostics'


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
