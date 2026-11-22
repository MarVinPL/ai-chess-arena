# Worker release and update policy (planned; not yet enabled)

## Trust and distribution

- Use **free GitHub Releases** for versioned, reviewed builds. Reserve tags `worker-vX.Y.Z` for this app; general Arena releases must not trigger Worker update notices. The current read-only prototype scans up to 100 recent GitHub releases and selects the highest stable `worker-vX.Y.Z` tag; older releases beyond that page may not be discovered. Use a dedicated signed Worker update feed before enabling installation. Publish only after PR review, tests, and explicit release approval; never use a moving development branch as an update source.
- For Windows, macOS and supported Linux bundles, use **Tauri 2 Updater** with a pinned public verification key embedded in the application and detached signatures generated with a protected private signing key. A HTTPS URL alone is not sufficient to authenticate updates.
- **Do not commit private signing keys**, credentials, release tokens, or build secrets. Prepare a documented, revocable signing-key custody and rotation procedure before enabling updates.
- Pin the updater endpoint to the project's approved GitHub Releases metadata; do not allow a remote worker or an arbitrary URL to supply an update manifest.
- Verify platform/architecture, signature, and strictly newer version before installing. Reject unsigned and mismatched artifacts; handle unavailable metadata and invalid signatures without changing the installed version.
- Windows installer and macOS signing/notarization prerequisites are platform-specific. Linux support depends on the package format; do not promise universal self-updates for `.deb` packages managed by the OS package manager.
- Android APKs require an Android-specific signed release/update flow, appropriate platform permission and user confirmation. **Do not use the desktop updater for Android or silently sideload APKs.** No Android build or updater has been verified.

## Safe lifecycle

- The GUI now checks for releases at startup at most once per 24 hours by default (locally stored opt-out; manual check remains available). This is read-only and does not install anything. Default to notifying about an available update; permit the user to disable automatic checking. Never start installation while a chess task is running.
- When busy, show `Update ready — waiting for task completion`; allow installation only after a coordinator-confirmed terminal state and safe checkpoint. A crash or unknown job state must not be interpreted as completion.
- Preserve token credentials and user preferences across normal updates; test migration before release. Provide release notes, current version, progress and actionable error states.
- Rate-limit periodic checks and avoid update checks in the hot path of inference. No paid hosting or update service is required.

The Rust prototype includes a **fail-closed installation gate** (`may_install_update`) with unit tests and a read-only `update_installation_readiness` Tauri command. This command always returns `can_install=false` until authenticated scheduler idle state and signature verification are integrated. It cannot install updates; no scheduler-verified idle signal or signature verifier is connected yet.

The authenticated `POST /api/remote-workers/update-safety` endpoint currently returns `state: unknown` and `install_allowed: false` for valid worker tokens. This is deliberate: the live server does not yet have a durable coordinator job registry to confirm idle after restarts. The in-memory `WorkerSessions.update_safety_state` helper is not sufficient for a production installation decision.

## Acceptance tests before enabling

1. Valid signed newer release is discovered; user can choose install/defer.
2. Invalid signature, wrong platform, downgrade, interrupted download and network timeout are rejected without losing settings.
3. Busy worker defers installation; idle worker can update; restarted worker resumes safe state.
4. Token persists, and no secrets appear in logs, update URLs or UI.
5. Windows, Linux, macOS and Android paths are tested separately on actual supported targets.

**Current state:** design specification only. No updater endpoint, release signing key, auto-install action or production deployment has been configured.

## Durable coordinator state (prototype)

`remote_worker_job_registry.py` stores job assignments and coordinator-confirmed
terminal transitions in SQLite. Unknown workers and expired unresolved jobs are
`unknown`, including across process restarts. The authenticated
`/api/remote-workers/update-safety` endpoint exposes this advisory state, but
always returns `install_allowed: false`. No worker-facing route can mark a job
terminal or initialize an idle state. The production scheduler is **not yet
wired** to these writes; do not treat the registry as operational evidence of
idle until scheduler integration, restart recovery and concurrency tests pass.

## Durable coordinator job safety registry (prototype)

`remote_worker_jobs.py` provides SQLite-backed coordinator-only `assign`,
`complete`, `invalidate`, and `state` operations. Unknown workers, expired jobs,
revoked/invalidated states, and uncommitted transitions never grant idle status.
A coordinator must atomically coordinate its actual job assignment/terminal
commit with these transitions before trusting `idle_confirmed`; this integration
is **not implemented**. The HTTP update-safety endpoint still returns `unknown`
for every authenticated worker. The registry alone does not enable installation.

`WorkerSessions` can now optionally receive `durable_jobs=DurableWorkerJobs(...)`.
It records assignment before in-memory dispatch, confirms terminal results,
and invalidates the registry on revocation. This remains an integration
prototype: coordinator recovery, concurrent writers, cross-store atomicity,
and live scheduler wiring are not yet complete. Do not expose persisted
`idle_confirmed` as permission to install until these are resolved.

Concurrent assignment is now guarded by SQLite `BEGIN IMMEDIATE`, and
`recover_after_restart()` invalidates all persisted idle/running assertions.
**Startup integration is mandatory and not wired:** the actual coordinator
must call recovery before accepting traffic, and reconcile its active jobs.
Until that happens, the HTTP endpoint must continue to return `unknown`.

### Server startup integration

`server.py` now invokes `WorkerJobRegistry(DB).recover_after_restart()`
after `db_init()` and before starting background threads or the HTTP listener.
This is the registry currently read by `/api/remote-workers/update-safety`
(`remote_worker_job_registry.py`), separate from the earlier experimental
`remote_worker_jobs.py` session registry. Startup invalidates persisted idle
and running assertions, and failures abort startup. This does not reconcile
or resume interrupted jobs; installation remains disabled.

Review finding: a terminal acknowledgement arriving after its assignment
lease expired must not restore `idle_confirmed`. `WorkerJobRegistry.record_terminal`
now requires an unexpired deadline; expired work remains `unknown` pending
explicit coordinator reconciliation. Covered by regression test.

The isolated startup-block integration tests in
`tests/test_remote_worker_server_boot.py` execute the real `server.py` startup
statements against a temporary SQLite database with stubbed background
services and HTTP listener. They verify recovery occurs before accepting
traffic and a recovery failure aborts startup. They do not restart the
production systemd unit or exercise the complete server process.

Security review correction: startup must invalidate **both** independent
SQLite registries (`remote_worker_job_state` and `worker_job_safety`) before
starting background threads or HTTP. Neither registry is currently a complete
scheduler; a confirmed idle state is not authorization to install updates.

Result and failure transitions in the experimental `WorkerSessions` now
confirm durable completion before setting the in-memory `committed` flag or
recording metrics. Failed durable completion invalidates the safety state and
leaves the job uncommitted; regression tests cover both transitions. Metrics
are best-effort after the durable transition. This is not an atomic result
commit or a complete reconciliation protocol: the two SQLite registries remain
independent, and neither is wired to a production remote-job scheduler.

GUI regression tests now distinguish read-only outbound HTTPS verification
from prohibited remote job execution. `localStorage` may contain only update
check preferences/timestamps; worker credentials must remain in the OS keyring.
This is a static-code regression guard, not a substitute for runtime security
review. The two durable registries still need a single authoritative scheduler
and reconciliation policy before live remote execution.

The API compatibility facade `WorkerJobRegistry` now delegates to
`DurableWorkerJobs`, using **only** `worker_job_safety` as the authoritative
SQLite table. Legacy `remote_worker_job_state` rows are ignored rather than
migrated as trusted idle assertions. Server startup performs one canonical
recovery. This does not create a production scheduler or reconcile in-flight
remote jobs; installation remains disabled.

Security review: mixed API/scheduler concurrent assignments serialize through
SQLite and allow only one claim; recovery rejects late terminal results.
Regression tests cover both. **Remaining blocker:** simultaneous coordinator
processes are not fenced by an ownership epoch or exclusive process lease.
A second coordinator boot could invalidate another coordinator's active work.
Do not enable remote execution or updater installation until process ownership,
job reconciliation and durable result commit are designed and tested.

Review regression tests verify that concurrent assignments through the API
facade and the scheduler registry serialize to exactly one accepted job, and
that coordinator restart invalidates late completion of a pre-restart job.
**Deployment limitation:** SQLite transaction serialization does not enforce
single-coordinator ownership. A second coordinator process restarting while
another serves traffic could invalidate live claims; no distributed lease or
leader election is implemented. Keep remote job dispatch disabled until
single-writer process ownership and reconciliation are designed and tested.

Concurrency audit: `BEGIN IMMEDIATE` serializes competing assignments and
terminal writes to the canonical SQLite table. Fractional lease deadlines are
preserved without integer rounding. **Remaining NO-GO:** concurrent server
processes have no leader election/fencing epoch; one process restarting can
invalidate another process's live assignments. Run a single coordinator until
process ownership/fencing and real job reconciliation are implemented. The
current HTTP endpoint still denies update installation.

### Single-host coordinator ownership

Server startup now acquires an exclusive POSIX `flock` on
`<DB>.coordinator.lock` **before** invalidating persisted worker claims or
starting any threads/listener. The file descriptor remains open for the
process lifetime. A competing server using the same local database and lock
path fails before recovery; kernel releases ownership on process exit.
Multiprocess tests verify rejection and acquisition after exit. This only
protects cooperating processes on one host/local filesystem: it is not a
cross-host fencing token, does not repair orphaned jobs, and does not make
remote execution or updater installation production-ready.

### Crash-recovery and result-commit audit (NO-GO)

The canonical `worker_job_safety` table persists only worker/job IDs, state,
lease deadline and update time. `WorkerSessions.accept()` validates a UCI move,
then commits `idle_confirmed` through `DurableWorkerJobs.complete()` before
returning the move to its caller. A crash between these operations loses the
accepted move: neither the FEN, result payload nor scheduler application is
committed in the same SQLite transaction. `WorkerMetrics` writes a separate,
best-effort table and cannot serve as a replayable result log. Restart recovery
intentionally invalidates claims but cannot reconstruct lost moves. The live
server has no `WorkerSessions` scheduler integration.

Required before production remote chess jobs: a coordinator-owned durable
assignment/result journal, atomic transition from validated result to
replayable pending-application state, idempotent scheduler application with a
stable job ID, and crash/restart tests at every transaction boundary. The
local `flock` prevents concurrent cooperating server processes on one host,
but does not supply distributed fencing. Update installation and remote job
dispatch remain disabled until these conditions are verified.

### Prototype durable result staging

`remote_worker_result_journal.py` adds a coordinator-only staging journal.
`stage_validated()` must be called **only after** independently validating a
worker result against its original job and board. It atomically records a
replayable JSON result and transitions the worker safety lease to terminal.
Identical retries are idempotent; conflicting retries and expired leases fail
closed. Recovery preserves staged results while invalidating worker idle claims.
This is deliberately **not wired** to `WorkerSessions` or the live chess
scheduler: result application, its acknowledgement, and recovery reconciliation
still require an idempotent scheduler transaction before production use.

### Atomic scheduler application adapter (prototype)

`WorkerResultJournal.apply_atomically(job_id, callback)` executes a caller's
scheduler-state update and the result acknowledgement inside **one SQLite
transaction**. Replays after acknowledgement are no-ops; a callback failure
rolls back both writes and leaves the result pending. The callback must write
through the supplied SQLite connection only, perform no external side effects,
and independently validate the job-to-game mapping and expected board state.
Tests use a disposable `demo_moves` table, **not** the live tournament
checkpoint. The actual chess runner still uses a separate checkpoint writer;
production remote dispatch remains NO-GO until that writer and result journal
are integrated transactionally and restart-tested.

### Transactional tournament checkpoint adapter

`WorkerResultJournal.apply_checkpoint_atomically()` now combines the existing
journal acknowledgement with `tournament_checkpoint.save_in_transaction()` in
one SQLite transaction. Checkpoint compare-and-swap checks the expected SHA-256
and stored payload integrity before writing. A conflicting or corrupted
checkpoint leaves the journal entry pending; replay after a successful commit
is a no-op. Tests cover success, replay, and checkpoint conflict.

**Integration boundary:** the adapter accepts a coordinator-constructed
checkpoint payload; it does not independently verify a worker's chess move,
validate pairing ownership, or attach to `SequentialChessRunner.publish_move`.
The live tournament runner still uses its original checkpoint save path.
Production remote chess execution remains disabled until the runner dispatch,
board validation, crash recovery and replay ownership are integrated and tested.

### Chess move verification at checkpoint application

Before applying a staged result, the adapter now checks the original
checkpoint hash, active game phase, unchanged pairing, exact one-move append,
legal UCI against the original FEN, and the resulting FEN/side-to-move.
Illegal or mismatched moves roll back acknowledgement and checkpoint changes.
This does **not** yet establish a durable job-to-pairing/FEN assignment record;
that mapping, full checkpoint metadata integrity, and live runner dispatch
must be completed before enabling remote chess execution.

### Durable job-to-position binding (prototype)

`remote_worker_job_binding.py` provides `BoundWorkerResults.assign_bound()`:
it atomically reserves the worker and records the tournament ID, pairing ID,
checkpoint SHA-256 and FEN in SQLite. `apply_bound()` rechecks the binding and
checkpoint inside the same transaction as chess move validation, checkpoint
update and result acknowledgement. Restart replay and stale-position tests
pass. This is coordinator-side code only; it is **not** wired to the live
`SequentialChessRunner` or its job dispatch. Production remains NO-GO.

### Optional SequentialChessRunner inference seam

`SequentialChessRunner` now accepts an optional `move_provider` callback.
Without it, all inference still uses the existing local `LlamafileRuntime`.
The callback receives an isolated board copy, player, stop event and inference
options; its returned move must be legal and timing metadata valid. Tests play
a complete four-ply checkmate with a simulated provider and reject an illegal
provider move. The callback is **not** connected to network transport,
`BoundWorkerResults`, or the production server, and no remote worker has been
activated. Full remote execution requires coordinator dispatch, durable
job-result bridging, checkpoint-aware progress handling, and crash/restart
end-to-end tests before production approval.

### Offline runner-to-journal bridge and restart test

`remote_worker_runner_bridge.py` connects the optional runner move-provider
interface to durable job binding, validated result staging, and atomic
checkpoint updates. It uses an injected **offline** inference function, not
network transport. A test plays the four-ply Fool's Mate, stops after two
persisted moves, simulates coordinator restart, and resumes from the SQLite
checkpoint to checkmate with no duplicate moves. The test explicitly
re-confirms the worker as idle after restart; this is a **test-only state
transition**, not an implemented worker reconciliation protocol.

**NO-GO for production:** the live `server.py` still invokes its original
local runner and `publish_move` callback. There is no authenticated network
transport, live dispatch, durable worker re-confirmation, or production
replay coordinator. The bridge must not be enabled alongside the existing
checkpoint writer for the same game without a single-writer integration.

### Fail-closed transport interruption

The offline bridge now records the in-flight job ID **before** calling its
injected inference transport. A transport exception cannot silently erase the
job identity or allow a second task through the same bridge. Regression tests
confirm that the durable worker reservation remains running, the checkpoint
is unchanged, and restart marks the worker unknown. There is still no
production-grade authenticated worker reconciliation; this does not enable
remote dispatch.

### Single outstanding job per chess position

The durable binding now rejects a second assignment for the same tournament,
pairing and checkpoint digest while an earlier job is running or its staged
result awaits application. This prevents competing workers from generating
moves against the same authoritative FEN. A simulated inference transport
failure leaves the job in flight, the worker non-idle, and the checkpoint
unchanged. Stale jobs remain blocked pending explicit coordinator
reconciliation; automatic retry/reassignment is intentionally not enabled.
Production remote execution remains NO-GO pending authenticated transport,
reconciliation and live server single-writer integration.

### Checkpoint metadata protection

Bound result application now rejects changes to immutable checkpoint fields,
including tournament configuration, schedule, entries and player identities.
The only permitted changes are a single validated move, one matching move
duration, resulting FEN/turn and checkpoint update timestamp. Move ply,
color, record FEN and duration must match the legal move. A negative test
confirms that a forged schedule field cannot be committed or acknowledged.

The live production server remains on the local runner path. Authenticated
transport and safe post-restart worker reconciliation remain outstanding.

### Restart reconciliation guard (offline only)

`BoundWorkerResults.confirm_idle_after_restart()` allows an `unknown` worker
to become `idle_confirmed` only when the coordinator caller supplies both an
independently verified operator confirmation and a freshly authenticated,
reconnected worker confirmation. It rejects duplicate reconciliation and
workers with unapplied journal results. The test harness supplies these
booleans explicitly; **there is no implementation yet that authenticates or
verifies the underlying evidence**. This method must remain inaccessible to
worker-controlled HTTP requests and is not production-ready. A real operator
approval flow, fresh worker challenge/response and auditable evidence are
required before enabling it.

### Crash replay of staged move without re-dispatch

`RemoteRunnerBridge.replay_staged()` can commit a journaled pending result
following coordinator restart, using a coordinator-reconstructed move record.
It reuses the durable binding, legal-move checks and checkpoint compare-and-swap.
Replay never marks a worker idle: after restart its safety state remains
`unknown`, and subsequent assignments remain blocked pending independent
reconciliation. Duplicate replay is rejected. This is an offline recovery
primitive, not a network recovery endpoint or automatic production resume.

### Result staging / reassignment race guard

`stage_validated()` transitions worker safety to `idle_confirmed` before its
chess checkpoint result is applied. A worker with any pending result must not
receive another bound job even if its safety state appears idle. Assignment
now checks `worker_result_journal` for pending applications under the same
SQLite `BEGIN IMMEDIATE` transaction. A regression test verifies the worker
cannot be reassigned until that result is committed. This does not replace
post-restart reconciliation or authenticated transport; production remains
NO-GO.

### Restart: prevent competing assignment to an unknown worker's position

A coordinator restart clears the `job_id` in `worker_job_safety` and marks
workers `unknown`. The outstanding-position check must join safety by worker
identity (not only the now-cleared job ID), so an unresolved worker continues
to block a second worker from receiving the same checkpoint/FEN. A regression
test verifies that a restart followed by a competing assignment fails closed.
This is a guard, not an automated worker reconciliation or production enablement.

### Authenticated result intake (internal only)

`remote_worker_authenticated_ingest.py` provides a coordinator-only
`AuthenticatedResultIngest.accept()` path. It authenticates a worker's
SQLite-backed token, verifies durable worker/job binding and unexpired lease,
reconstructs the board from the committed checkpoint, validates legal UCI,
rechecks token validity inside the SQLite write transaction, and atomically
stages the result while transitioning worker safety state. Negative tests
cover invalid/revoked credentials, foreign jobs, illegal moves and duplicates.

No HTTP route is exposed by this module. TLS, request-size and rate limits,
network authorization, production runner integration and operator-approved
reconciliation remain outstanding. **NO-GO production.**

### Opt-in loopback HTTP intake prototype

`remote_worker_http_ingest.py` provides a standalone, **non-autostarting**
HTTP adapter for authenticated result intake. `create_loopback_server()`
binds only to `127.0.0.1`; requests must use the internal result path,
`application/json`, an explicit body length of at most 2048 bytes, and a
rate-limited peer. Errors do not reveal token or job validity. An integration
test starts a temporary loopback server, submits a valid legal move, rejects
invalid credentials and duplicate submissions, checks body size and rate
limits, then shuts down the listener. No route is added to `server.py`.

**NO-GO production:** reverse-proxy TLS/access restrictions, authenticated
worker enrollment and delivery, multi-process rate limiting, server runner
integration, and restart reconciliation still require review. This listener
must not be exposed to a public interface.

### HTTP listener resource limits

The opt-in loopback listener now applies a five-second socket read timeout to
avoid indefinitely stalled request handlers. Its in-memory per-IP rate limiter
caps distinct active peers at 1024 and evicts expired entries. A test covers
peer saturation and expiry. These protections do **not** replace TLS, reverse
proxy connection limits, production request accounting, or safe worker
reconciliation. The listener remains disabled by default and is not wired to
`server.py`.

### Caddy HTTPS loopback-only example

`deploy/remote-worker/Caddyfile.loopback.example` is an **offline-only**
Caddy configuration using `tls internal`, explicit `bind 127.0.0.1`, a
POST-only result route, a 2048-byte body limit, and a loopback-only upstream
on port 19444. It is deliberately **not imported** by production Caddy.
Caddy `adapt` was checked on the existing VPS and confirms the listener
is `127.0.0.1:19443`, not the wildcard interface. A regression test checks
the adapted listener. Internal CA TLS is suitable for a local smoke test,
not remote worker deployment; external trust, identity and transport
requirements remain unresolved. No Caddy reload or production service
change was made.

### Isolated HTTPS-to-SQLite smoke test

`tests/test_remote_worker_https_smoke.py` starts a temporary Caddy instance
with its own config, internal TLS CA and loopback listener, plus a temporary
loopback intake service and SQLite database. A real HTTPS request reaches the
intake endpoint, authenticates the worker and stages one legal chess move.
Invalid credentials and duplicate submissions are rejected. The test tears
down both services; no production Caddy config or process is reloaded.
The test uses `curl -k` **only** for a self-signed loopback smoke test; real
worker deployment must verify the server certificate and hostname. This is
not production network authorization, and production runner integration is
still pending.

### Live checkpoint shape compatibility test

`tests/test_remote_worker_production_checkpoint_shape.py` runs the actual
`SequentialChessRunner` with the offline remote bridge against a checkpoint
containing the same metadata fields as the production server payload:
`tournament`, `entries`, `schedule`, `pairing`, both player snapshots,
`game_started`, `moves`, `move_times`, `fen`, `side_to_move`, `updated_at`.
The test commits a legal remote move and asserts immutable metadata is
preserved and the staged journal is empty. This validates the payload
contract only; `server.py` still uses its local move provider and its
independent checkpoint writer. A single-writer handoff and authenticated
network dispatch remain prerequisites before any production enablement.

### Accepted service separation decision

The owner approved deploying the Remote Worker Gateway as a **separate
systemd-managed service** on the existing VPS. Architecture, operational
boundaries, single-writer requirement, default-off policy and zero-cost
constraints are recorded in
[`service-architecture-decision.md`](service-architecture-decision.md).
This is a documented decision only; no unit has been installed or started.

### Separate gateway process and systemd template

`remote_worker_service.py` is a manually started entrypoint for the
loopback-only result intake, with explicit database and port arguments,
preflight checks for an existing initialized SQLite schema, and graceful
SIGTERM/SIGINT shutdown. The template
`deploy/remote-worker/ai-chess-remote-worker.service.example` documents the
proposed independent systemd unit with restart, privilege, network and
resource restrictions. The unit is **not installed or enabled**. Its example
DB path, writable directory, account permissions and limits must be checked
against the actual production environment before installation. The gateway
currently receives results only; outbound job delivery and coordinator
single-writer integration remain pending. Tests verify fail-closed startup
and template guardrails on the existing VPS.

### Standalone process lifecycle smoke test

`tests/test_remote_worker_service.py` now launches the actual gateway
entrypoint in an independent subprocess with a temporary SQLite database and
an ephemeral loopback port. It checks invalid token rejection, authenticated
legal move intake, duplicate rejection, durable result staging, and graceful
SIGTERM exit. The process is always cleaned up after the test. This does not
install or enable the systemd unit and does not touch production data.

### Automated systemd unit verification

`tests/test_remote_worker_systemd_template.py` copies the proposed unit to a
temporary `.service` file and runs `systemd-analyze verify`, then checks the
independent gateway entrypoint and restart, resource, loopback and hardening
settings. This is a syntax/static policy test, **not** a systemd-managed
runtime test. No unit was installed, enabled or started on the VPS.

### Disposable systemd lifecycle integration test

`tests/test_remote_worker_systemd_transient.py` starts the gateway using a
randomly named **transient user-systemd unit**, with a temporary SQLite DB,
random loopback port and no restart policy. It verifies an authenticated
legal move reaches the durable journal, stops the transient unit and checks
that it is inactive. The test does not install, enable or change any
production unit. This tests actual systemd process management, not the
full production service template or external HTTPS deployment.

### Authenticated assigned-job retrieval (loopback only)

`remote_worker_job_delivery.py` exposes a read-only coordinator-assigned
job lookup for an authenticated worker. The opt-in HTTP gateway now accepts
`POST /internal/remote-worker/job` with worker ID and token, returning the
existing job ID and bound FEN, or `null` if none is eligible. It never assigns
jobs, modifies worker state, or leaks another worker's job. Token revocation,
lease expiry, checkpoint integrity and pairing are checked. This is a local
integration step only: the existing Caddy example deliberately exposes only
the result endpoint, so the job endpoint is **not externally reachable**.
Production dispatch, access policy and coordinator single-writer handoff
remain prerequisites for enabling remote execution.

### Isolated full job roundtrip

`tests/test_remote_worker_job_roundtrip.py` exercises a real loopback HTTP
exchange: coordinator creates a durable position binding, authenticated worker
retrieves its assigned FEN, submits a legal move, result is staged, and the
coordinator applies it with checkpoint compare-and-swap. Duplicate submission
is rejected, repeated application is idempotent, and exactly one move is
committed. This uses a temporary database and HTTP server, **not** the
production tournament scheduler, remote HTTPS exposure or automatic dispatch.

### Coordinator-side staged-result wait adapter

`remote_worker_coordinator_transport.py` adds a bounded, interruptible wait
for a coordinator-assigned result in the durable SQLite journal. An offline
integration test feeds an authenticated result from a separate thread into
`RemoteRunnerBridge`, which applies the move to the tournament checkpoint.
Timeout does not release the reservation or reassign the position. This is a
coordinator-side test adapter only: `server.py` still uses local inference,
there is no automatic production scheduling, no network worker executor,
and no deployed gateway. Before production use, the bridge must avoid
restaging an already staged result and acquire single-writer ownership.

### Offline runner ↔ HTTP worker integration

`tests/test_remote_worker_coordinator_http_runner.py` runs the real
`SequentialChessRunner` through the coordinator-owned `RemoteRunnerBridge`.
A simulated worker polls the actual loopback gateway for its assigned job,
submits `e2e4` through authenticated HTTP, and the coordinator waits for the
staged result before atomically committing one move to the tournament
checkpoint. Local inference is explicitly prohibited by the test runtime.
This is an isolated proof of the coordinator/worker boundary, **not** a
production `server.py` dispatch integration, an external TLS deployment, or
a real LLM worker execution. Production single-writer ownership and restart
reconciliation still require implementation/review.

### Fail-closed dispatch policy (not yet connected to server.py)

`remote_worker_dispatch_policy.py` defines an explicit local/remote mode gate.
Remote selection requires an opt-in flag, coordinator ownership, a ready
worker and exclusive remote checkpoint-writer ownership. It never silently
falls back to local if a remote job is unavailable or fails. The current
production `server.py` still uses the local runner; wiring the mode switch
requires an explicit lifecycle design for acquiring/releasing coordinator
ownership and avoiding its existing direct `tournament_checkpoint.save` path.

### Explicit runner selection boundary

`remote_worker_runner_selection.py` provides a fail-closed runner factory
selector. Local remains the default; remote selection requires the opt-in
flag, coordinator ownership, ready worker, and exclusive remote checkpoint
ownership. Only the selected runner is constructed, and remote failures never
implicitly construct a local fallback. The **production server.py still uses
the existing local runner**. Wiring this selector into production requires
explicit lifecycle ownership, checkpoint writer replacement and restart tests.
