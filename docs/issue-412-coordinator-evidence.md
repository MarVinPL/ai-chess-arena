# #412 — coordinator journal evidence v1

POST /api/remote-workers/update-journal-evidence accepts exactly worker_id,
token and a 32-character lowercase hexadecimal nonce. Existing bounded JSON
validation and peer/authenticated identity rate limits apply. Use HTTPS at the
existing TLS ingress; no new listener or ingress configuration is introduced.

Authentication and worker state are read in one SQLite read transaction.
A positive snapshot requires a recent (at most 30 seconds old), structurally
idle safety row and explicit absence of holds (including expired holds),
pause requests, resume checkpoints, inference assignments and idle-release
recovery receipts. Missing schema is represented by null, never false.
Unknown, stale and inconsistent state cannot be considered idle.

The response binds contract_version=1, authenticated=true, source=coordinator,
worker_id, nonce and checked_at_unix_ms. It contains presence flags and no
credentials, hold capabilities, job identifiers or checkpoint content.
Authentication failures do not echo the nonce. Errors are generic.
The existing HTTP handler sets Cache-Control: no-store.

This is a point-in-time advisory observation, not an authorization:
snapshot_only=true, initialization_allowed=false, install_allowed=false.
The transaction ends before sending the response. Another coordinator
transaction may change state immediately afterwards. A client must reject
unknown versions, absent fields, mismatched nonces/identities and stale/future
timestamps. A fresh client nonce must never be reused. Repeating a request
does not consume any capability or authorize a mutation.

#414 must implement a coordinator-side fenced/CAS authorization bound to
identity and state, covering the initialization commit and preventing conflicting
assignment/reservation transitions; a repeated read or local mutex alone is
not sufficient. No such authorization is implemented or claimed here.
First-registration and unknown-state recovery are #415/#416; missing job
rows are deliberately not treated as idle. Older servers without this endpoint
must fail closed. No migration, installer changes or deployment in #412.

Verification: HTTP tests with temporary SQLite databases and loopback server,
including independent concurrent writer under WAL.

Review hardening: the route opens SQLite in read-only URI mode, with query_only,
without invoking schema-creating constructors. Missing tables cannot be silently
recreated by this endpoint. Snapshot timestamp is captured before the first
read; snapshots taking more than three seconds are rejected and token expiry
is checked again before response. Revocation or coordinator changes after the
snapshot remain a reason to require fresh fenced authorization in #414.
