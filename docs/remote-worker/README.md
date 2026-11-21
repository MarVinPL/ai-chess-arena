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
