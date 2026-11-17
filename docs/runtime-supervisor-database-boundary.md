# Runtime supervisor database boundary — Issue #157

## Status

Architecture decision record / implementation gate. This document does not enable runtime execution, provision host resources, or change production configuration.

## Decision

Do not give the privileged supervisor write access to the Arena application database or its containing directory. Keep the canonical SQLite database owned and writable by the unprivileged API process.

The preferred design is a narrow, API-owned AF_UNIX lease broker. The supervisor remains the authority for systemd unit creation, stop, cgroup verification and handoff cleanup. The API remains the authority for durable application state and all SQLite writes.

A separate root-owned lease database is not selected as the first option: it would split the job claim/cancellation state from the unit lease and introduce cross-database crash windows that would need a second reconciliation protocol.

## Broker contract

The API broker must expose only these fixed operations; clients supply no SQL, paths, unit names, state transitions outside the enumerated set, or arbitrary payloads:

- `persist_start(job_id, unit_name)`: one `BEGIN IMMEDIATE` transaction verifies the job exists, is `running`, has `cancel_requested=0`, and has no existing lease; it validates the deterministic unit name for that exact job and inserts `start_pending`.
- `set_state(job_id, unit_name, state)`: compare-and-set the exact existing lease and allow only the supervisor state machine's enumerated transitions.
- `get(job_id)`: return the minimal lease record for that job.
- `load_recoverable()`: return bounded, ordered records in recoverable states. The broker must support pagination or a bounded response strategy; it must never silently truncate recovery work.
- No generic database, schema, migration, or delete operation is permitted.

The broker validates every field, rejects duplicate JSON keys and oversized frames, applies bounded read/write timeouts, authenticates the peer with `SO_PEERCRED`, and returns generic errors without leaking DB content or paths. It must serialize conflicting lease operations through SQLite transactions and enforce idempotency for retries after ambiguous IPC failures.

The supervisor keeps its existing read-only authorization connection for job, grant, batch, preflight and settings checks. It no longer opens the application database in read/write mode. Lease reads and writes go through the broker so there is one durable source of truth. Any filesystem access needed for SQLite read-only WAL operation must be separately measured and granted read-only; the supervisor must not be able to create, replace, truncate or chmod the database or its sidecars. The disposable-VM test must cover both rollback-journal and WAL modes, including a live writer.

## Startup and request choreography

The current startup order calls supervisor recovery before the HTTP listener starts. A broker cannot simply be added after that point.

Required order for a future implementation:

1. API initializes and validates its canonical database using its existing unprivileged ownership.
2. API starts the dedicated lease-broker listener on a pre-provisioned socket and waits for confirmed bind/listen readiness.
3. Supervisor service starts independently and exposes its existing supervisor socket.
4. API asks the supervisor to recover exact persisted units. During this request the supervisor calls back to the already-running API broker to load leases, stop exact units, persist state transitions, and verify cleanup.
5. Only after supervisor recovery returns complete, validated evidence may API executor orphan reconciliation run.
6. Only after both recovery phases succeed may the public HTTP listener and background workers start.

The broker listener must run on a separate thread or event loop from the startup thread: API startup is synchronously waiting for supervisor recovery, while the supervisor's recovery request synchronously calls back into the broker. A single-threaded listener would deadlock.

The systemd dependency graph and readiness contract must guarantee that the API broker is available before supervisor recovery is requested. Failure or timeout at any stage blocks public API startup and executor reconciliation. Broker shutdown must be coordinated after the supervisor can no longer issue callbacks.

## Crash and race requirements

- If API commits `start_pending` but the supervisor crashes before systemd start, recovery sees the lease and safely inspects/stops the exact deterministic unit.
- If supervisor's persist request times out after API commit, retry must be idempotent: same job and exact unit returns the existing matching lease; a different unit is rejected.
- If cancellation commits before `persist_start`, the broker rejects the lease. If lease persistence commits first, the supervisor must still revalidate authorization immediately before systemd start; stop/recovery must handle a cancellation arriving in that window.
- A state update must compare job ID and unit name, and must not resurrect a terminal lease.
- A missing broker, malformed response, incomplete recovery page, stale socket, peer mismatch, or DB busy/IO error fails closed.
- Recovery completion means every recoverable lease has been reconciled and every relevant cgroup is confirmed empty before handoff cleanup. A count-only response is not sufficient unless it is tied to a complete server-side recovery run and cannot mask pagination failure.
- The API database schema remains API-managed. The broker must never create or migrate tables.

## Required validation before implementation is considered production-ready

- Protocol unit tests: strict schemas, duplicate keys, framing, peer credentials, timeouts, generic errors, and bounded pagination.
- Transaction tests against temporary SQLite DBs: concurrent persist, cancellation-vs-persist ordering, idempotent retries, exact-unit mismatch, state transition rejection, WAL/busy behavior.
- Fault injection at every IPC boundary: disconnect before commit, after commit/before response, during recovery, and during state updates.
- Startup integration tests proving broker readiness precedes supervisor recovery and executor reconciliation never runs after incomplete recovery.
- A disposable-VM test with the actual systemd sandbox, UID/GID identities, socket permissions and SQLite WAL mode. No production deployment may substitute for this test.
- Independent security review of the broker protocol and systemd policy.

## Current implementation state

The current `SQLiteRuntimeUnitStore` still writes the canonical Arena DB. Its schema initialization is now non-mutating when used by the privileged service, but that does not solve the write-permission boundary. The supervisor service must remain non-deployable until this ADR is implemented and the disposable-VM validation passes.

No host service, account, group, directory, socket, ACL, cgroup, firewall rule, model process or production configuration is changed by this design.
