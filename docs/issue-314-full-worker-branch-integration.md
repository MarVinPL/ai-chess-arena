# Issue #314 — full #296 integration review

Date: 2026-11-23. Scope: integration of the existing Android Worker
Draft branch into the separate web/backend Draft branch only. **No deployment.**

## Routing and ownership

- The single `server.Handler.do_POST` entrypoint forwards all machine routes
  to `worker_backend.dispatch_worker_post` **before** browser authentication.
  There is no second inline Worker authenticate handler or panel-cookie
  fallback. Unknown Worker routes fail closed with HTTP 404.
- Twelve exact POST Worker paths are registered in the machine router:
  authenticate, job-poll, ready, job-result, update-pause-poll, board-view,
  update-resume-status, update-safety, model-source, update-reserve,
  update-release, update-hold-status.
- These routes delegate to the existing coordinator's durable lease, STOP/ACK,
  model admission, hold-token and checkpoint logic, not to another game state.
- The authenticated panel GET routes for remote-worker metrics and the remote
  worker view remain available; the old tests were updated to inspect the
  separated backend modules rather than inline server handlers.
- The existing Android Worker files, workflows, and safety protocol are kept;
  only text-only conflict resolution and test reference updates were needed.
  Test fixture paths use isolated SQLite and staging directories.

## Isolation and checks

- Sandbox source checkout on the existing VPS; no files from the live Arena
  installation or production databases were changed.
- Three conflicted files were reconciled: `server.py`,
  `tests/test_hf_candidate_downloads.py`, and
  `tests/test_remote_worker_model_registry.py`.
- The panel GET statistics route and navigation were preserved.
- Git diff conflict/whitespace checks and Python compilation passed.
- Python full suite in the isolated combined checkout: **1071 passed, one
  skipped, 82 subtests passed**, one existing SyntaxWarning.
- One test run is not Android physical-device validation; CI on the exact
  published integration commit is required before further progression.

## GO / NO-GO

Ready for CI and independent review of the combined Draft PR branch.
**NO-GO for merge to main or production deployment** until:
1. GitHub CI on the exact integration commit passes (Python and applicable
   Android workflows).
2. Cross-component integration reviews verify token/cookie isolation,
   the 12 Worker routes, checkpoint CAS, update hold expiry and STOP/ACK.
3. Isolated staging performs an end-to-end Worker update interruption,
   verified checkpoint, authenticated resume, and rollback test.
4. The stacked #296 parent PR and other dependencies are reconciled to the
   project one-Issue/one-PR policy before merging to main.

Nothing in this integration authorizes updating the production APK or service.

## Integrated staging regression — 2026-11-25

The combined branch now has 13 exact Worker POST routes (including
`native-stop-report`). The earlier twelve-route inventory above is historical.
The new `tests/test_issue_314_worker_update_lifecycle_e2e.py` exercises the
actual HTTP handlers on loopback and a temporary SQLite database, not the
production database. Native STOP and installed APK evidence, and trusted
resume transport acceptance, are simulated at their coordinator boundary.

A regression exposed HTTP 200 for a stale v3 lease after resume. The resume
CAS now checks the original STOP lease digest and durable native report,
rotates the execution capability atomically with reopening the job, updates
the assigned timeout, and removes stale result receipts and STOP reports.
Old capability submissions return HTTP 409; the authenticated job poll
returns the new capability, which accepts a valid result. Legacy inference
assignments without fencing remain blocked pending explicit migration.

Evidence: five lifecycle scenarios passed, including rollback during hold
creation, rollback after lease rotation, competing resume attempts, missing
STOP evidence and old-result fencing. Full local regression: 1174 tests and
141 subtests passed, 55.08 s. The existing cache repair on 4676ef7 passed
Python, Android ARM64 build and offline Gradle cache verification in CI.
CI must be repeated on the new lease fix commit.

**NO-GO for merge/deployment**: a real authenticated device resume transport
is not wired, and physical Android interruption/install/recovery/rollback
has not been validated. SQLite rollback is not APK rollback. Dependency PR
reconciliation and final review remain required. No production changes.
