# Issue #192 — remote inference worker (preparation only)

**Status:** protocol skeleton and validation tests only. No pairing, network endpoint,
worker executable, remote model transfer, scheduling, or production deployment yet.

## Architecture

1. A friend **voluntarily** runs a worker locally and initiates an outbound TLS/WSS
   connection to the existing VPS; no inbound ports on the friend's router.
2. The VPS pairs the device with explicit operator approval and a revocable,
   short-lived worker-only credential. This authentication is **not implemented**.
3. The VPS assigns an idempotent move job with version, opaque job ID, FEN,
   SHA-256 model digest, token budget and timeout. No shell commands, file paths,
   administrative credentials, database or private files are ever part of a job.
4. Worker checks model availability and resource consent locally, runs inference,
   and returns a UCI move. The VPS checks job correlation and legality against
   its authoritative game board before committing a move exactly once.
5. On disconnection: mark job uncertain, fence stale results, retry with a new
   attempt or fall back to VPS only if resources and model permit. Persist a
   checkpoint before scheduling the next turn.

## MVP limits and prerequisites

- One complete move per worker, **not** splitting model tensors across machines.
- Windows worker first, Linux second; no hidden autostart or elevated privileges.
- Explicit local CPU/GPU/RAM/time caps; local stop and server-side revocation.
- Only user-approved models; verify hash and license before any optional download.
- Start with an isolated test server and simulated worker; do not enable on prod.
- Threat model: stolen tokens, replayed results, malformed FEN/UCI, hostile job
  payloads, slow/disconnected workers, resource exhaustion, duplicate move commits.
- Current `remote_worker_protocol.py` is a **defense-in-depth input validator**, not
  an authentication, sandbox, network service, or production-ready solution.

## Local validation

Run `PYTHONPATH=. python -m unittest tests.test_remote_worker_protocol` with
`python-chess` installed. All components are free/open-source.

## Cross-platform GUI draft (2026-11-21)

`remote_worker_gui/public/index.html` is an accessible, responsive, Polish-language
**offline GUI mockup** suitable for embedding in a future Tauri WebView on
Linux, macOS and Windows. It exposes connection/pairing fields, local CPU/RAM/GPU
preferences and a status dashboard. The resource sliders are interactive, but
**Pair / Start / Pause / Stop / Disconnect are intentionally disabled** until a
real authenticated transport and worker lifecycle exist. No external scripts,
network calls, telemetry, model download or filesystem access are performed.

The preferred future packaging is Tauri + Rust, but Rust/Cargo are not yet
installed on the test VPS, and no native app has been compiled or verified.
Do not treat the HTML mockup as a working agent.

## In-memory session prototype

`remote_worker_sessions.py` now provides one-time pairing codes (5-minute TTL),
worker tokens (1-hour TTL), revocation, job expiry, correlation, board-state
fencing and single-acceptance result checks. Tokens and codes are hashed in
memory; no secrets are persisted. This is a **local coordinator building block**,
not a deployable authentication endpoint. Admin authorization for approval,
rate limiting, persistent audit, TLS/WSS, worker-side GUI bindings, inference,
crash recovery and integration with tournament checkpoints remain to be built.

## Tauri 2 desktop shell (prototype)

`remote_worker_gui/src-tauri/` now contains the initial Tauri 2/Rust desktop
shell, a local-only `worker_status` command, minimal window capability and a
CSP prohibiting outgoing network connections. It is deliberately **not** a
functional distributed worker: pairing, credential storage, backend inference,
resource enforcement and scheduling must be implemented and security-reviewed
before the disabled GUI actions can be enabled.

To compile locally once Rust, Node and the Tauri 2 platform prerequisites are
installed, run `cd remote_worker_gui && npm install && npm run dev`. Do not
install tools or incur paid developer-signing costs without approval. Linux
WebKitGTK system packages and macOS signing/notarization requirements depend on
platform. `bundle.active=false` intentionally avoids claiming release-ready
installers. The VPS currently lacks Cargo, so no native build is claimed.
