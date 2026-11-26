# Issue #314: stacked integration verification against Worker PR #296

Date: 2026-11-23. Scope: **isolated VPS dry run**, not a merge to GitHub
`main`, a production deployment, or physical Android staging.

## Commit boundary

- Worker source PR #296: `da7edbedfe8c9f4d6527027a34d0f40ad2cf2fac`,
  branch `feat/issue-295-native-model-cache`.
- Web/Worker separation PR #316 at start of dry run:
  `485459ca4a2c8d692dfa9132f8444fd87cd010cc`,
  branch `refactor/issue-314-separate-web-worker-backends`.
- Both branches were 0 commits behind `main` when verified.
- PR #296 remains stacked on another feature branch and is not yet
  independently merged into `main`; PR #316 stays Draft.

## Procedure and result

An ephemeral clone under `/tmp` on the existing Debian VPS was used.
The #296 head was merged into the #316 head with `--no-commit --no-ff`.
There were exactly three conflicted files:

1. `server.py` (two conflict blocks): kept the single #316 Worker router
   for POST; retained authenticated #296 Worker metrics and worker-page
   GET routes. The old inline Worker POST handlers were not retained.
2. `tests/test_hf_candidate_downloads.py` (one block): kept the
   staging-directory isolation that never touches protected production paths.
3. `tests/test_remote_worker_model_registry.py` (one block): kept the
   assertions pointed at the extracted model-source module rather than old
   inline `server.py` conditions.

Before this check, six #296 test modules were updated to recognize **both**
the legacy inline endpoints and the new extracted modules, while retaining
their machine-token, STOP/checkpoint and no-unauthorized-assignment assertions.
All 55 targeted legacy tests passed independently on #296.

**Dry-run combined result: 1071 passed, 1 skipped, 82 subtests passed,
0 failed**, in approximately 44 seconds. One existing Python
`SyntaxWarning` remains in the Android versioned APK filename CI test.
The assembled `server.py` had exactly one `worker_backend.dispatch_worker_post`
call and zero inline `/api/remote-workers/authenticate` branches.
The temporary clone was automatically removed.

## Security and integration constraints

- All 12 machine POST routes are inventoried by
  `tests/test_issue_314_worker_full_route_inventory.py`; browser
  sessions must never authorize them.
- The coordinator retains sole ownership of leases, board state, checkpoint
  verification and START/STOP transitions.
- `update-reserve`, `update-release` and `update-hold-status` rely on
  the same durable SQLite transaction registry; expiry never releases a hold.
- Model-source cannot accept a caller-selected digest or URL; admission is
  checked against the model registry and active lease is rechecked.
- Read-only update status cannot authorize installation.
- A staged Android client must still demonstrate actual STOP/ACK, persisted
  checkpoint and safe resume with current coordinator locks.

## Release decision

The **source compatibility dry run passed**, but **production readiness is
not proven**. Do not squash or merge the entire stacked PR #296 history into
PR #316 merely to silence conflicts; this would duplicate pending #296 work
in the PR against `main`. Keep the existing one-Issue/one-PR boundaries.
Finalize the integration after upstream Worker PRs are merged in their own
order, review the resulting final diff, run GitHub CI on the actual resulting
commit, then isolated staging E2E. No main, VPS service or production data
was changed by this dry run.

## Integration checkpoint — 2026-11-26

This supersedes the earlier pending-CI and source-synchronization status,
not the unresolved staging or dependency-ownership requirements.

- Main: `af23d648fbe10ed40815e95c76ae6361235796a9`.
- Combined #316: `064a43fa0d2edeeba514465f533f6953466675cb`; main is an
  ancestor, and the worktree was clean before the CI trigger cleanup.
- Verified ancestor heads: #197 `e21f825`, #292 `40e75fe`, #294 `1756eb5`,
  #296 `32996ac`. All four PRs remain open drafts; #197 still has a
  conflict against its own stacked base.
- Local strict Python regression: 1174 passed, 141 subtests, 54.12 s.
- Exact-head GitHub Actions passed: Python 45812635604, Worker selective
  builds 45812636106 (ARM64 unsigned/debug APK), and shared Gradle cache
  45812635500 (warmup and restore). Signed APK, Linux and Windows skipped.
- Pre-merge CI cleanup removes temporary stacked branch triggers from
  `worker-multiplatform.yml` and `python-tests.yml`; normal main PR checks
  and Python feat/**/fix/** push checks remain. No signing conditions,
  build commands, cache configuration or job permissions changed.
  Targeted regression: 42 passed. Full regression and exact-head CI must
  be repeated after this cleanup is committed.
- #308 checkpoint staging is a separate branch at `62b34a4`, not part
  of #316. Its local Python/Rust/ARM64 checks do not prove physical-device
  transport, actual coordinator authorization or engine restoration.

### Decision

NO-GO pending final review and isolated staging evidence, a decision on
independent upstream PR ownership/merge order, and CI for the final
reviewed commit. #308–#311 work must not be silently treated as completed.
The full combined diff at 064a43f was 230 files, +30534/-648; the broad
stacked scope is not an independent Issue #314-only change. The historical
loopback test is not a physical Android install/rollback test. No production
deployment, APK installation, Issue closure or merge authorization follows
from this report.
