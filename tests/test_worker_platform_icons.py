"""Platform-specific assets must not trigger unrelated Worker builds."""
from pathlib import Path
import struct
import re
import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / '.github/workflows/worker-multiplatform.yml'
ICONS = 'remote_worker_gui/src-tauri/icons/'

@pytest.mark.parametrize('name,group,trigger', [
    ('icon.ico', 'windows', False),
    ('32x32.png', 'linux', False),
    ('128x128.png', 'linux', False),
    ('128x128@2x.png', 'linux', False),
    ('android-pawn.svg', 'android', True),
    ('icon.png', 'shared', True),
])
def test_platform_icon_routing(name, group, trigger):
    src = WORKFLOW.read_text()
    path = ICONS + name
    filters = src.split('          filters: |\n', 1)[1].split('      - name: Select', 1)[0]
    groups = dict(re.findall(r'^            (\w+):\n((?:              - .*\n)+)', filters, re.M))
    assert set(groups) == {'shared', 'linux', 'windows', 'android'}
    assert [key for key, value in groups.items() if f"'{path}'" in value] == [group]
    triggers = src.split('\npermissions:', 1)[0]
    for event in ('push', 'pull_request'):
        event_src = triggers.split(f'  {event}:\n', 1)[1].split('\n  pull_request:', 1)[0]
        assert (f"'{path}'" in event_src) is trigger
    assert ICONS + '**' not in src


def test_windows_icon_has_valid_directory_and_png_payload():
    data = (ROOT / ICONS / 'icon.ico').read_bytes()
    assert struct.unpack_from('<HHH', data) == (0, 1, 1)
    width, height, _, _, planes, bits, size, offset = struct.unpack_from('<BBBBHHII', data, 6)
    assert (width, height, planes, bits) == (64, 64, 1, 32)
    assert offset >= 22 and offset + size == len(data)
    assert data[offset:offset+8] == b'\x89PNG\r\n\x1a\n'
    assert struct.unpack_from('>II', data, offset + 16) == (64, 64)
