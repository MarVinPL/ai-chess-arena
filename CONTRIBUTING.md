# Contributing and repository workflow

## Work queue order

- Always start with the oldest unfinished item across **open pull requests and open issues**, ordered by creation time.
- Finish or explicitly resolve that item before moving to the next one. Do not skip an older PR to start a newer issue.
- At the start of each work cycle, inspect both open issues and open PRs; a PR may be the active implementation of an older issue.

## Keep every PR synchronized with main

- Before continuing work on an existing PR, compare its head branch with the current `main` and record its ahead/behind state.
- If `main` has advanced, synchronize the PR branch with `main` before implementing more changes.
- Re-check and synchronize after meaningful changes land on `main`, and always immediately before final review and merge.
- Resolve conflicts only on the PR's own branch. Preserve both the PR's intended changes and all relevant updates from `main`; never reset, overwrite, or force-push an unverified branch to make synchronization easier.
- After synchronization, rerun relevant tests and syntax checks, inspect the complete diff, and verify that the PR still implements exactly one Issue.
- Before merging, verify that the PR is not behind the current `main`. If safe synchronization cannot be completed, stop and report the blocker rather than merging a stale branch.
- Synchronization is maintenance of the same PR branch; it never authorizes combining Issues. **One Issue = one branch = one PR** remains mandatory.

## One issue = one pull request

- Every implementation issue must have exactly one dedicated PR.
- Every such PR must close exactly one issue using `Closes #<issue-number>`.
- Never combine multiple issues in one PR.
- Never split one issue across multiple PRs.
- Do not add unrelated scope to an existing PR. Create a separate issue and a separate PR instead.
- Keep the issue/PR relationship explicit in the PR description and issue discussion.

## Completion and release

- A task is not complete merely because code exists in a branch or on a server.
- Run relevant automated tests, syntax checks, and diff checks; review the final diff before merge.
- Merge is distinct from deployment. Do not deploy, restart services, modify databases, or change active tournament state without the required explicit approval.
- Report what was changed, tested, merged, and deployed separately. Never imply deployment from a successful merge.

## Tournament and runtime safety

- Treat active and planned tournaments, their rosters, pairings, and results as protected state.
- Do not change tournament state, model files, inference runtime, or database contents as part of unrelated repository work.
- Put follow-up behavior or additional improvements in their own issues and PRs.

## Periodic VPS-to-main drift detection and controlled synchronization

- Periodically perform a read-only comparison of the deployed VPS revision with the current `main`, including repository status, tracked and untracked changes, and the running service/release identity where available.
- Distinguish an expected deployment lag from uncommitted VPS edits, a divergent checkout, and a deployment that was intentionally held back.
- When drift is detected, report the exact revisions, ahead/behind commits, affected paths, local modifications, operational risk, and whether an active tournament or inference process could be affected.
- Detection is read-only. Never run automatic `git pull`, `reset`, `clean`, `stash`, checkout, deployment, migration, or service restart as part of monitoring.
- Before any approved synchronization, inspect and preserve all local edits and untracked files, confirm a usable backup/rollback path, and assess active tournament/runtime state. Never overwrite changes merely to make VPS match `main`.
- Synchronizing VPS code requires explicit user approval. Deployment, service restart, database migration, and tournament-state changes each require their own explicit approval.
- Repository changes that implement this workflow still follow the dedicated branch/PR process, and the PR must be synchronized with current `main` before merge.
