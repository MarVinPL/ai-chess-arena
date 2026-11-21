import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / 'remote_worker_gui'


def test_tauri_scaffold_configuration():
    cfg = json.loads((ROOT / 'src-tauri' / 'tauri.conf.json').read_text())
    caps = json.loads((ROOT / 'src-tauri' / 'capabilities' / 'default.json').read_text())
    assert cfg['build']['frontendDist'] == '../public'
    assert cfg['app']['security']['csp'].find("connect-src 'none'") != -1
    assert cfg['bundle']['active'] is False
    assert caps['permissions'] == ['core:default']
    assert (ROOT / 'src-tauri' / 'src' / 'lib.rs').exists()


def test_no_remote_commands_enabled():
    code = (ROOT / 'src-tauri' / 'src' / 'lib.rs').read_text()
    assert 'worker_status' in code
    assert 'std::process::Command' not in code
    assert 'reqwest' not in code
