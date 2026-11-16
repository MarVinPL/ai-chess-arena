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

`RuntimeSupervisorIPCServer` requires all three handlers to be injected. It does
not construct a database store, backend, systemd unit, directory or socket
parent. The parent directory must be prepared by a separately authorized host
provisioning step. The module is not wired into `server.py` or production
services, and its tests use temporary local sockets only.

## Production gate

Before wiring this channel into the Arena API, implement and review the trusted
job-to-spec handler, persistent lease lifecycle, exact systemd effective-property
normalization, artifact handoff, socket ownership/mount visibility, private
network bridge and crash recovery. Then run integration tests under dedicated
UIDs in a disposable environment. Do not create host identities, directories,
sockets or systemd units as part of this code-only step.
