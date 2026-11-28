# Native STOP recovery validation (#445)
POST /api/remote-workers/native-stop-recovery uses the existing opt-in phone gateway.
Fields: worker_id, token, nonce (fresh 32 lowercase hex), job_id, request_id,
lease_token. Authenticate the coordinator using the configured HTTPS origin and
normal certificate validation; the client must bind the durable report to that
origin (#446), never follow a cross-origin redirect or trust the source label alone.
The coordinator authenticates the worker and checks the requested STOP, running
unexpired job and v4 lease in a single read-only SQLite snapshot. No schema
initialization or report write occurs on this route. Admission uses existing
peer and authenticated identity rate limits.
Success: authenticated=true, source=coordinator, worker_id, nonce, job_id,
request_id, state=stop_recovery_validated, install_allowed=false,
dispatch_allowed=false. Match all echoed identifiers against the request.
401 means authentication failed, 409 means invalid/stale state; malformed,
unavailable or other responses must fail closed. Success is only a snapshot
validation, never an ACK, receipt, checkpoint or permission to dispatch/install.
No report deletion or delivery is implemented here (#327).
