# Runtime supervisor database boundary — Issue #157

## Status

Architecture decision record / implementation gate. This document does not enable runtime execution, provision host resources, or change production configuration.

## Decision

Do not give the privileged supervisor write access to the Arena application database or its containing directory. Keep the canonical SQLite database owned and writable by the unprivileged API process.

Implement a narrow, API-owned AF_UNIX lease broker. The supervisor remains the authority for systemd unit creation, stop, cgroup verification and handoff cleanup. The API remains the authority for durable application state and all SQLite writes.

The first implementation is in `runtime_lease_broker.py`: fixed JSON actions, duplicate-key rejection, `SO_PEERCRED` root-UID allowlist, bounded frames/timeouts, exact deterministic unit names, idempotent pending-lease persistence, compare-and-set state updates, and fail-closed bounded recovery (maximum 256 records; no truncation). The API starts the broker before requesting supervisor recovery. The privileged service uses `RuntimeLeaseBrokerClient` and no longer opens the lease store read/write.

A separate root-owned lease database is not selected as the first option: it would split the job claim/cancellation state from the unit lease and introduce cross-database crash windows that would need a second reconciliation protocol.

## Broker contract

The API broker exposes only these fixed operations; clients supply no SQL, paths, arbitrary unit names, state transitions outside the enumerated set, or arbitrary payloads:

- `persist(job_id, unit_name)`: one `BEGIN IMMEDIATE` transaction verifies the job exists, is `running`, has `cancel_requested=0`, and has no existing lease; it validates the deterministic unit name for that exact job and inserts `start_pending`.
- `set_state(job_id, unit_name, state)`: compare-and-set the exact existing lease and allow only the supervisor state machine's enumerated transitions.
- `get(job_id)`: return the minimal lease record for that job.
- `load_recoverable()`: return bounded, ordered records in recoverable states. The broker must support pagination or a bounded response strategy; it must never silently truncate recovery work.
- No generic database, schema, migration, or delete operation is permitted.

The broker validates every field, rejects duplicate JSON keys and oversized frames, applies bounded read/write timeouts, authenticates the peer with `SO_PEERCRED` (root UID only), and returns generic errors without leaking DB content or paths. It serializes conflicting lease operations through SQLite transactions and supports idempotent retries of the same `start_pending` lease. Recovery is bounded at 256 records and rejects larger sets instead of truncating them. State changes compare job ID, exact deterministic unit name and prior state.

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

The API process now initializes and owns `runtime_unit_leases`; the privileged supervisor uses the broker client and retains only its read-only authorization connection. Its code is packaged as a deterministic zipapp by `tools/build_runtime_supervisor_bundle.py`; the root entrypoint verifies the fixed path, root ownership, non-writable mode and SHA-256 before importing the service modules. The API broker socket is fixed at `/run/ai-chess-arena-api/lease-broker.sock`, mode `0660` (API-group writable), and accepts only root peers. The API service must provision its own `RuntimeDirectory=ai-chess-arena-api` with mode `0750` (or a private `0700` directory) before launch; the broker verifies that the parent is a real directory owned by the API UID and is not group/world writable. The supervisor's existing `/run/ai-chess-arena-runtime` directory remains separate.

This implementation is not yet cleared for deployment. The disposable-VM test must prove that the real API service can create its broker socket, the root supervisor can connect, unprivileged peers are rejected, the supervisor cannot write the database or SQLite sidecars, and read-only authorization works in both rollback-journal and WAL modes. It must also exercise cancellation-vs-persist races and supervisor/API restart ordering.

No host service, account, group, directory, socket, ACL, cgroup, firewall rule, model process or production configuration is changed by this design.


## SQLite journal-mode compatibility matrix

The privileged supervisor opens the Arena database with SQLite URI mode=ro,
PRAGMA query_only=ON, and PRAGMA trusted_schema=OFF. Its service identity is
root:debian with only CAP_CHOWN and CAP_DAC_READ_SEARCH; in particular it does
not have CAP_DAC_OVERRIDE or CAP_FOWNER. It can read the API-owned database
but must not modify the database, truncate it, chmod it, or create files
beside it.

Validated using a disposable database and that exact UID/GID/capability
boundary:

- Rollback journal (DELETE): read-only supervisor query succeeds while the
  API writer holds a RESERVED transaction; SQL writes are rejected. After
  writer commit and close, a fresh read-only connection sees the committed
  value and no journal sidecar is required.
- WAL with live writer and existing -wal/-shm: read-only query succeeds while
  the API writer is open; SQL writes and filesystem write/truncate/chmod/create
  attempts are rejected.
- WAL after clean last-writer shutdown, with both -wal and -shm absent:
  read-only open fails closed (attempt to write a readonly database) because
  SQLite needs to create WAL shared-memory state but the supervisor cannot
  write to the API-owned database directory.

Therefore the current read-only supervisor boundary is compatible with the
application's default rollback-journal mode. WAL mode is not an unconditional
supported deployment mode: it requires an explicit lifecycle design that
guarantees the required sidecars are available for every supervisor read,
without granting the supervisor write access to the database directory. Do
not switch the production database to WAL as a workaround. The application
source currently does not enable WAL; this matrix used disposable databases
only and did not inspect or change the production database.
