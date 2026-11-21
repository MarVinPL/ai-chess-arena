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


def test_local_status_bridge_is_read_only():
    cfg = json.loads((ROOT / 'src-tauri' / 'tauri.conf.json').read_text())
    html = (ROOT / 'public' / 'index.html').read_text()
    assert cfg['app']['withGlobalTauri'] is True
    assert "invoke('worker_status')" in html
    assert "invoke('pair')" not in html
    assert "invoke('start')" not in html

The desktop GUI now queries the local Rust `worker_status` command through the
Tauri IPC bridge and renders disconnected state; this does not establish a VPS
connection. Browser-only preview reports that the native backend is unavailable.

### Pairing coordinator prototype (not exposed over network)

`remote_worker_pairing.py` implements in-memory, single-use 8-character pairing
codes (5-minute default expiry), explicit approval, short-lived bearer tokens
(1-hour default), and revocation. Tokens and codes are stored only as HMAC
hashes. Each worker has at most one pending code; incorrect worker IDs are
limited to five attempts per code. This is **not yet an authenticated API**:
`approve` and `revoke` must only be called after integrating administrator
identity and authorization on the VPS. No network listener is provided; no
worker is paired and no inference is enabled by this module. Before production:
use durable transactional state across restarts, TLS/WSS transport, per-source
rate limiting, replay resistance, authenticated admin actions, and audit logs.
