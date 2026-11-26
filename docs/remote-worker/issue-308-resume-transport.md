# Issue #308 — authenticated checkpoint staging

Status: implementation candidate, not production-ready. Parent EPIC #307.
Base: PR #316 commit `064a43fa0d2edeeba514465f533f6953466675cb`.

## Protocol and storage

- Private coordinator authorization reuses the trusted resume offer and requires verified installed identity, stopped engine and checkpoint evidence. Delivery authorization defaults to 300 seconds, bounded to 1–600 seconds.
- `POST /api/remote-workers/update-resume-status` advertises an authorized offer; `POST /api/remote-workers/resume-checkpoint` requires machine authentication, nonce, worker, job, STOP request and checkpoint digest. Both validate the current fenced STOP snapshot. No caller-supplied path or URL is accepted.
- The lease evidence is the original stopped assignment's lease digest, not a new running lease. Unknown safety state and absence of update/model maintenance holds are mandatory. Delivery neither grants a lease nor acknowledges restoration.
- Native HTTPS client rejects redirects, bounds response size, verifies identity, nonce, schema version, Base64 bytes, length and SHA-256. No checkpoint bytes or machine credential are returned to the WebView.
- App-private `filesDir/worker-resume-checkpoints` stores up to 1 MiB per checkpoint. Filename is SHA-256 of the colon-separated coordinator endpoint hash, worker ID, STOP request ID and checkpoint digest, plus `.json`. Create-exclusive temporary file, file fsync, atomic hard-link publication without replacement, and directory fsync. Exact repeated bytes are accepted; conflicting bytes are rejected. No automatic retention or deletion yet.
- UI snapshots connection identity, invalidates readiness when identity changes and ignores superseded async responses. Staging is not engine acceptance; the resume button remains blocked for pending checkpoints.

## Evidence (2026-11-26)

- Python: `python -m pytest -q -W error::SyntaxWarning` — 1182 passed, 141 subtests, 57.39 s, exit 0.
- Rust: `CARGO_BUILD_JOBS=2 cargo test --locked --offline --lib` — 79 passed, exit 0; native integrity, idempotence, conflicting bytes and cross-coordinator isolation covered.
- Android: `cargo check --locked --offline --lib --target aarch64-linux-android` — exit 0, NDK 27.2.12479018. This is a compilation check, not an installed APK test.
- Python HTTP tests use isolated SQLite and loopback HTTP; trusted authorization is simulated. Coverage includes wrong token (401), wrong worker/job/request/hash (409), expired delivery authorization, changed STOP lease, missing STOP evidence, corrupt/oversize/consumed checkpoint, repeated delivery and blocked polling. UI tests simulate native invocation and stale responses.

## Remaining acceptance blockers

- Wire private authorization into a real trusted coordinator path with verified installed identity and STOP evidence; no public endpoint may accept these attestations.
- Actual interrupted HTTPS transfer/retry on Android, full APK CI for this branch, physical-device verification, and explicit verification of any required 403 response remain outstanding. Current tests do not establish those results.
- #309 must implement actual engine restoration/ACK; #310/#311 cover further regression/device evidence. No running lease from staging alone.
- Review retention of stale local checkpoints and expired authorization records. In-flight delivery authorized at snapshot time is not a revocable stream.
- PR #316 and older stacked dependencies remain unmerged. Do not merge this branch or deploy it to production. Open one PR to main after dependencies are reconciled, without duplicating their changes.
