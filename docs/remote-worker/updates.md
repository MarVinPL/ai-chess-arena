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
