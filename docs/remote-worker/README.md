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

### Decision: token-based onboarding (2026-11-21)

The user selected a simpler workflow: administrator issues a worker token; the
volunteer pastes the token once. The desktop app may remember it using the OS
credential store (Windows Credential Manager, macOS Keychain, Linux Secret
Service), never plaintext configuration, browser localStorage, or logs. Token
input is masked. Disconnect must delete locally stored credentials; the VPS
must support revocation and expiry. The existing pairing registry is legacy
prototype code and is **not** the selected onboarding workflow.

**Not implemented yet:** token storage, token issuance, TLS connection and
revocation API. GUI controls remain disabled until these are integrated and
tested. No secrets are currently saved by the app.

### Native credential storage

The Tauri Rust backend now uses the open-source `keyring` crate for system
credential storage. GUI actions `store_worker_token`, `has_saved_worker_token`,
and `forget_worker_token` save, detect, and delete credentials without exposing
stored token values to the frontend. A local desktop secret service/keychain
must be available; saving fails closed otherwise. On headless Debian with no
unlocked Secret Service, the application cannot persist credentials. This is
local storage only: no network verification or VPS connection is implemented.

### Local OS credential storage

The desktop GUI supports Save token, Check saved token and Remove saved token
using the Rust `keyring` library (Windows Credential Manager, macOS Keychain,
Linux Secret Service). The secret is never returned to JavaScript or written to
app configuration. The user must explicitly opt into remembering the token.
The Save button does not authenticate or connect to the VPS. Linux desktops
must have a working Secret Service provider (e.g. GNOME Keyring or KWallet);
headless Xvfb alone is not sufficient to test persistent storage. On Linux,
macOS and Windows, real OS-keychain integration needs a user-session smoke test.
Removing the local secret does not revoke a server-side credential; server-side
revocation is a separate required feature.

### Durable coordinator-issued tokens (offline component)

`remote_worker_tokens.py` implements a SQLite-backed worker token registry.
An authenticated administrator can issue a random 256-bit token with bounded
TTL (default 30 days), rotate it, or revoke it. The database stores only a
SHA-256 digest, not plaintext; authentication uses constant-time comparison.
Reopening the registry after a process restart retains valid credentials.
Only issue the token over an administrator-authenticated channel, and protect
the SQLite file with OS permissions and backups. **No HTTP API is exposed by
this module**; authentication, authorization, TLS, rate limiting, audit logging
and wiring into the arena scheduler still need integration and testing.

### Statistics per agent/token — Issue #192

`remote_worker_metrics.py` stores coordinator-confirmed terminal job events in
SQLite, deduplicated by job ID. A stable `worker_id` links statistics across
token rotation/revocation. Metrics: total tasks, successes, errors, cancellations,
wall time, CPU/GPU compute milliseconds, chess moves and last activity. The
admin-authenticated `/api/remote-workers/metrics` endpoint defaults to all-time;
`/remote-workers` is the read-only panel table. Neither route returns tokens.

**Integration gap:** The scheduler does not yet emit coordinator-verified job
completion events to `WorkerMetrics.record()`. Consequently the panel correctly
shows zero data until actual remote jobs are integrated. CPU/GPU time requires
trusted coordinator-side measurement; do not accept self-reported values from
untrusted workers as authoritative. No production deployment was performed.
