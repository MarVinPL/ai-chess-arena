# Issue #314 — Integration audit against Worker Draft PRs

Date: 2026-11-23. Scope: source/route integration review only; **not** Android or staging E2E.

## Examined
- #316: browser backend separation (`worker_backend.py`, `web_panel_*.py`, `server.py`)
- #197: Android Worker updater/protocol, touches `server.py`, machine jobs and Android components
- #292: STOP/ACK fencing; edits `remote_worker_inference_delivery.py`, `remote_worker_protocol.py`, `remote_worker_update_pause.py` and tests; **no direct shared files with #316**
- #296: Android native model cache/updater plus machine endpoints directly in `server.py`

## Proven intersections and integration risks

1. **Server.py overlap (#197 ↔ #316 and #296 ↔ #316).**
   #316 replaces the original inlined Worker dispatch with
   `worker_backend.dispatch_worker_post`. #296 separately adds machine
   endpoints within `server.Handler.do_POST`. Integrating #296 by simply
   taking #316's `server.py` risks dropping worker endpoints and bypassing
   the new module ownership. Do not accept a merge resolved by 'ours/theirs'
   without a route-by-route reconciliation.
2. **Unreachable duplicate auth handler in #296.**
   #296's `server.Handler.do_POST` has two branches matching
   `/api/remote-workers/authenticate`, with a return on the first branch.
   The second is dead code and needs consolidation under one audited
   machine-token authentication boundary.
3. **Worker route coverage.**
   The current #316 base `remote_worker_phone_api.ROUTES` covers
   `job-poll`, `ready`, `job-result`, `update-pause-poll` and
   `board-view`, with a separate `authenticate` route.
   Draft #296 adds additional update-safety/hold/reserve/release/resume,
   model-source and other machine operations. Each new path must have
   an explicit token, lease, nonce, body-limit and failure-contract review.
   Worker-token endpoints must be dispatched before panel auth and must not
   acquire panel privileges.
4. **#292 does not collide at the file level with #316** at this check,
   but changes coordinator authority around STOP/ACK. Ensure new worker
   endpoints cannot signal a trusted STOP/ACK merely from an HTTP success or
   a native local quiescence indication. The coordinator alone commits
   authoritative lease/checkpoint transitions.

## New regression guard
`tests/test_issue_314_worker_pr_integration_boundary.py` checks that
panel and Worker route registries are disjoint, that `server.py` uses one
Worker dispatch and that draft-only update endpoints have not been
accidentally exposed on #316's base. When #296 is integrated, expand this
test **in the same integration PR** with expected machine handlers instead
of deleting the security checks.

## Required before READY
- [ ] Rebase/synchronize each integration branch with current `main`
  without overwriting distinct Worker behavior.
- [ ] Resolve duplicate `authenticate` in #296 and enumerate every
  Worker machine endpoint in one auditable routing/authorization registry.
- [ ] Verify session-cookie cannot authenticate any Worker endpoint,
  and machine token cannot authorize panel resources or write operations.
- [ ] Test stop/ack, resumption, checkpoint, lease CAS and duplicate replies
  across the final combined code, not separately passing Draft PR checks.
- [ ] Review full diff and CI on final merged integration HEAD.
- [ ] Run isolated staging smoke tests and rollback checks.
- [ ] Keep production deployment and APK installation behind separate approval.

## Decision
**NO-GO for merging #316 solely on the basis of its current green Python CI.**
That CI validates #316 in isolation, not the integrated versions of #197,
#292 and #296. No production or Android E2E verification is claimed.

## Fail-closed namespace fix on #316

Before any browser session check, `worker_backend.dispatch_worker_post`
now **rejects unknown `/api/remote-workers/*` routes with HTTP 404**.
Previously, these unknown machine paths fell through to panel-session
routing and returned a panel-login response. This fix reserves the machine
namespace and prevents future accidentally added panel handlers from
acquiring Worker paths. Existing recognized routes retain their token-only
and feature-flag behavior. An unrelated route still falls through normally.
The regression test covers an unknown Worker URL carrying a browser cookie.

**#296 remains blocked:** its branch is 12 commits behind main and alters
`server.py` alongside main. No unverified merge resolution or duplicated
auth-handler removal was pushed to #296. The single-handler consolidation
must be performed after reconciling main and the stacked PR dependencies.

## Incremental resume-status migration — 2026-11-23

The upstream #296 selective Android APK build [45259697151](https://github.com/MarVinPL/ai-chess-arena/actions/runs/45259697151) completed successfully. A sandbox merge of #296 into #316 has one conflicting file (`server.py`, two hunks). Taking the #316 routing hunk alone dropped several Worker update endpoints and broke 12 selected tests — **do not merge by choosing either side wholesale**.

The first compatible operation has been migrated separately to `worker_update_resume_status_api.py`. Machine-token authenticated, bounded `POST /api/remote-workers/update-resume-status` is routed through `worker_backend.py` **before** browser-session checks. It delegates to the existing shared `WorkerUpdatePause.resume_status()` and cannot authorize APK installation or resume a job. Tests assert cookie/token isolation and response shape.

Still pending: separate safe adapters for `update-reserve`, `update-release`, `update-hold-status`; reconcile matching tests and audit full integration; physical Android/staging STOP/ACK; no changes to production or main. Other unregistered Worker paths remain HTTP 404. Until all routes are reconciled, merging PR #296 into #316 is **NO-GO**.

## Incremental update-safety migration — 2026-11-23

`worker_update_safety_api.py` now owns the read-only `POST /api/remote-workers/update-safety` route from #296, with exact machine-token authentication, validated lower-hex nonce, 2048-byte request cap, and bounded generic error responses. It reads the existing coordinator's `DurableWorkerJobs.update_safety_snapshot()` (the authoritative implementation also used by the #296 compatibility facade), so historical idle claims, active holds, pending STOP or resume and stale lease records cannot be treated as fresh idle evidence. `install_allowed` remains **false** regardless of reported state. Browser admin cookies never authenticate a Worker.

The route is explicitly registered in `worker_backend.py` before browser auth. Regression tests cover auth isolation, evidence shape, unknown state, malformed requests, size limits and confidential-error handling; the route-coverage guard now treats both `update-safety` and `update-resume-status` as audited exceptions while other new Worker operations remain blocked. Full combined PR #296/#316 integration remains **NO-GO** pending the model-source, update hold/reserve/release handlers, integration tests and staging review.

## Model-source migration — 2026-11-23

`remote_worker_model_registry.py` was copied byte-for-byte from the current PR #296 and remains the read-only, admission-backed SQLite model resolver. `worker_model_source_api.py` now dispatches `POST /api/remote-workers/model-source` from `worker_backend.py`, before any browser login checks. It requires a valid machine token, exact JSON fields (`worker_id`, `token`, `nonce`), a 32-character lowercase-hex nonce and a request of at most 2048 bytes. It obtains the SHA solely from the authoritative active assignment, never from a supplied URL/model ID. Admission is checked in SQLite; after metadata lookup, the current assignment/lease is rechecked to reject revoked/changed jobs. The response is public metadata, not an installer token or an assignment capability. Unadmitted models and stale assignments fail closed without returning metadata.

Regression tests cover admin-cookie isolation, active-assignment SHA binding, assignment change between checks, rejection of client-selected URLs/digests, malformed requests, and rejected model admission. The Worker update transaction endpoints `update-reserve`, `update-release`, and `update-hold-status` are now routed through the isolated backend with real SQLite tests; main/production are unchanged. Final combined Android Worker checkpoint and staging tests remain a merge prerequisite.

## Worker update reservation transactions migration — 2026-11-23

`worker_update_hold_api.py` now owns machine-token-only POST `/api/remote-workers/update-reserve`, `/api/remote-workers/update-release`, and `/api/remote-workers/update-hold-status`. They are explicitly registered by `worker_backend.py` *before* panel login. All require WorkerTokenStore validation, lowercase-hex 32-character nonce, bounded (2048-byte) JSON body and an Android-generated, previously journaled 64-character lowercase-hex `hold_token`. All success responses set `install_allowed: false`; only reserve returns the hold capability, while status never echoes it.

The single authoritative `DurableWorkerJobs` class enforces atomic `BEGIN IMMEDIATE` reservation on freshly confirmed idle, prevents duplicate reservations, refuses assignment while any hold exists (even after expiration), rejects mismatched hold tokens in constant time, and releases only through exact-token authenticated operations. Explicit release returns the worker to `unknown`, not idle. The HTTP layer never issues install permissions or claims a checkpoint STOP/ACK, and does not create another scheduler or DB.

Regression coverage uses real temporary SQLite/token stores rather than mocked transactions, including fresh/stale idle, duplicate or forged reserve/release, expiration with durable assignment block, status without leaking the token, invalid machine token with admin cookie, malformed payloads and missing pre-journaled hold tokens. **This is route-by-route integration**, not a completed merge of the full stacked #296 branch. Final combined STOP/ACK tests, staging, full diff and GitHub CI remain mandatory before merging PR #316.
