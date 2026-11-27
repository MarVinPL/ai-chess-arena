# Runtime supervisor IPC boundary (Issue #157)

`runtime_supervisor_ipc.py` defines a narrow AF_UNIX request channel between the
unprivileged Arena API and a separately privileged runtime supervisor.

## Protocol

One newline-terminated JSON object per connection, with exactly two fields:

- `action`: `start`, `stop`, or `status`;
- `job_id`: positive integer.

The API cannot send a unit name, command/argv, model path, artifact path, systemd
property, environment variable, or arbitrary endpoint. Duplicate JSON keys,
unknown fields/actions, malformed framing and oversized messages are rejected.
The server checks Linux `SO_PEERCRED` and accepts only the configured API UID.
Errors are sanitized; command output and exception messages are not returned.

## Trust boundary and required handler contract

The socket protocol is deliberately not an authorization decision. Before any
side effect, the privileged handler must independently load the job from its
trusted database and revalidate: job state, exact model/repository/revision and
artifact SHA-256, TEST_ONLY grant and expiry, batch approval and revocation,
execution feature flags, and cancellation state. It must derive the unit spec,
worker argv, artifact handoff and resource limits itself. Never accept these
values from the IPC request.

`RuntimeSupervisorIPCServer` requires its handlers to be injected. The root supervisor service composes the authority, lease broker client, exact-unit backend, handoff handlers and revocation watchdog; it does not create host identities or provision root directories. The API-side client is wired through the production-only dispatcher in `server.py`. The root supervisor service, bundle and socket parent are not installed/provisioned on the VPS by this PR; tests use temporary local sockets and fake backends.

## Production gate

Before production enablement, complete separate host provisioning and the documented internal audit of the trusted job-to-spec handler, persistent lease lifecycle, effective systemd properties, artifact/socket handoff, private-network bridge and crash recovery. Disposable Debian/systemd tests cover the integrated path, but do not replace host-specific validation. This PR does not install or enable the root supervisor service or create host identities/directories.
