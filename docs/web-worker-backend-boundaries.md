# Backend boundaries — Issue #314

This is the first stage of separating the AI Chess Arena web panel from its
Android/Remote Worker API. It changes module ownership, **not** deployment
topology: one Python HTTP service and one authoritative SQLite state remain.

## Request flow

```text
HTTP request -> server.Handler  (composition root; existing port and paths)
                |
                +-- POST /api/remote-workers/* --> worker_backend
                |   |-- authenticate: WorkerTokenStore machine token
                |   +-- opt-in routes: remote_worker_phone_api
                |            |-- DurableWorkerJobs / inference delivery
                |            +-- authoritative shared SQLite checkpoint/lease
                |
                +-- Web panel --> web_panel_auth (login, logout, origin/rate limits)
                    |-- arena_auth browser sessions
                    |-- web_panel_backend (exact static asset allowlist)
                    +-- web_panel_model_api (model permissions and reviews)
                    +-- web_panel_download_api (admin HF downloads)
                    +-- web_panel_tournament_api (admin tournament lifecycle)
                    +-- remaining admin/tournament handlers (incremental extraction)
                               |
                               +-- shared chess / model / checkpoint modules
```

## Security invariants

1. Worker endpoints must be dispatched **before** browser-session checks,
   using machine tokens for every recognized Worker operation. A valid web
   session cookie never authenticates the Worker.
2. The Worker endpoint allowlist is exact. Unknown routes must not expose
   privileged actions. The enable flag remains off unless explicitly enabled.
3. Panel assets remain behind browser-session authentication and use a
   fixed URL-to-file mapping; no user-controlled filesystem paths.
4. The Worker HTTP path does not commit chess moves or create/finalize
   authoritative checkpoints. Coordinator CAS and SQLite remain shared,
   without a duplicate database, allowlist or alternate result authority.
5. No new service, port, deployment, migration, APK or credential handling.

## Compatibility and migration

- Public endpoint paths and successful/failed response formats remain
  unchanged by this stage.
- `server.Handler.send_panel_asset` is kept as an adapter for existing
  callers/tests, with the implementation in `web_panel_backend`.
- `server.Handler.do_POST` delegates Worker authentication and the
  phone routes to `worker_backend.dispatch_worker_post` first; it then
  delegates browser login/logout to `web_panel_auth.dispatch_panel_auth_post`.
- Browser model-permission read/write and model-review-submission reads now
  delegate to `web_panel_model_api.py` behind the established session+Origin
  checks and reuse `league.connect()` and domain services. Other panel business
  API handlers remain inside `server.py` for now.
- Admin HF download start/cancel/resume/plan-refresh now delegate to
  `web_panel_download_api`, still behind session+Origin checks. Explicit
  confirmation requirements and status codes are unchanged.
  Further extraction must keep auth and CSRF checks in front of every panel
  action, and requires its own tests within Issue #314 before closing it.
- Unfinished Draft PR #197, #292 and #296 contain overlapping Worker
  changes. Reconcile their changes deliberately; do not overwrite them with
  this branch or infer runtime readiness from this refactor alone.

## Verification / rollback

Run `python -m pytest -q tests/test_issue_314_backend_boundaries.py
tests/test_panel_static_assets.py tests/test_remote_worker_phone_sandbox.py`,
then the full Python CI suite. Confirm worker token rejection and panel
cookie-only denial, and check that no route has changed semantics.

Revert the #314 PR on staging to restore the previous single-file dispatch
if regression is found. Production deployment requires separate approval.

## Tournament API extraction

`web_panel_tournament_api` owns the five administrative tournament POST
operations (create, start, stop, late admission, pre-start entries). Only the
browser-session and Origin-validated HTTP handler can invoke it. The live
thread and stop Event are **passed from server.py**, not copied into new
module globals. It does not create a second coordinator, worker control
path, SQLite database, checkpoint store or scheduler. The existing status
codes and responses continue through the same handler.

## HF review and audit boundary

`web_panel_hf_review_api` handles exactly the review, review-action and audit
POST requests. The browser session and Origin checks still run in
`server.Handler` before delegation; Worker tokens cannot access it. Existing
`league.connect()` is the only DB connection provider and the original
`hf_candidate_audit.AUDIT_LOCK` is retained for atomic exclusion, including
release on failure. This module does not admit models or modify lease logic.

## Model review and admission boundary

`web_panel_model_review_api` owns panel POST operations for model review
submission, review actions, and admission admit/revoke. It is called only after
browser session and Origin checks; Worker tokens are never accepted. It
retains the existing `model_allowlist.load_allowlist()` filtering, including
legacy exclusion of model ID 6 from review and the `eligible`/`restricted`
statuses. Authorization and admission decisions remain in the shared domain
modules, with `league.connect()` providing the authoritative connection.
No separate model registry or allowlist is introduced.

## Model storage administration

`web_panel_model_storage_api` implements only the authenticated panel's
`/api/models/download` and `/api/models/cleanup` operations. It preserves
explicit confirmations, shared `model_store` service and its
`MODEL_LIFECYCLE_LOCK` for non-blocking cleanup admission and guaranteed
unlock in `finally`. Browser-session and Origin checks remain in the
composition root; no Worker token is accepted, no separate filesystem
registry or model ownership is introduced.

## Model registration and license administration boundary

`web_panel_model_license_api` owns browser-admin model registration,
evidence retrieval, and manual license verification. The composition root
continues to require browser session and Origin checks; Worker tokens cannot
authorize these operations. The existing `fetch_hf_license_evidence`
implementation is injected by `server.py`, and `league.connect()` remains
the single model-version database connection. Confirmation, exclusion
checks, metadata evidence persistence and license updates remain unchanged.
The server retains the same HTTP route names and response status policy.

## Download status GET boundary

`web_panel_download_status_api` handles the five exact GET paths for HF
candidate download plans, history, events, files, and jobs. The main HTTP
handler checks the browser session **before** forwarding requests. All
responses retain their previous HTTP status behavior (including 404 for
missing jobs and 422 for missing event job_id or service errors), and use
existing `hf_candidate_downloads` state. No Worker-token fallback or
alternate download scheduler is introduced.

## HF status GET boundary

`web_panel_hf_status_api` owns five exact authenticated read paths for
candidate review, review queue, audit queue, hardware, and runtime preflight.
`server.Handler.do_GET` enforces browser session authentication before
calling it. It uses existing `league.connect()`, HF review, queue and
runtime services, preserving 422 errors for reviews and 503 errors for
queue/runtime snapshots. No Worker-token fallback or separate registry.

## Game history and PGN boundary

`web_panel_games_api` handles the three authenticated read variants:
`GET /api/games`, `GET /api/games/{id}` and `GET /api/games/{id}/pgn`.
The panel session is verified before delegation. The module reads the same
`arena.sqlite3` database path passed from `server.py` and retains the
legacy arbiter metadata, errors and PGN response headers. It does not serve
Worker machine tokens or create a second game store.

## Model inventory and Elo GET boundary

`web_panel_inventory_api` handles authenticated read operations for the
model inventory, model admission decisions, and PURE_AI / AI_ENGINE Elo
rankings. `server.Handler.do_GET` validates the browser session before
calling it, while the adapter reuses `league.connect()` and existing domain
services. Metadata normalization, budget fields, error status codes and
response formats remain compatible. No Worker-token fallback or separate
model registry is introduced.

## Tournament read boundary

`web_panel_tournament_read_api` owns browser-only `GET /api/tournaments`,
`GET /api/tournaments/{id}/view` and `GET /api/tournaments/{id}/preflight`.
The browser session is required before delegation. Listing and existence
checks reuse `league.connect()` with the unchanged SQL; live snapshots
and the preflight function are provided by the main server rather than
reimplemented. Legacy 404/422 errors are preserved. No second tournament
coordinator, Worker route or checkpoint store was introduced.

## HF catalog and stored audit reads

`web_panel_hf_catalog_api` serves `GET /api/hf-candidates` and
`GET /api/hf-candidates/audit`, only after browser-session authentication.
It reuses the existing candidate catalog, stored reports and reviews from
`league.connect()`, preserves the explicit response field projection and
404/422/503 error contracts. No second catalog, model allowlist or Worker
credential path is created.

## Storage overview GET boundary

`web_panel_storage_api` serves the existing `GET /api/storage` after the
browser session gate. It reuses `league.storage_status()` and the authoritative
`model_store.MANAGED_DIR`, returning the same managed GGUF filenames, byte
sizes and free space. The existing create-if-absent directory behavior and
failure semantics are retained. No Worker token or second model directory
is introduced.

## Passkey readiness history GET boundary

`web_panel_passkey_tests_api` owns the existing authenticated
`GET /api/passkey-tests` family, including optional `tournament_id`
filtering. It receives the original `arena.sqlite3` path from the server,
queries the existing `passkey_test_answers` table and preserves the response
columns, ordering and legacy error behavior. Browser-session authentication
runs before delegation; no Worker credential path or second test database.
