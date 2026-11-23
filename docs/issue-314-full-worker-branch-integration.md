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
