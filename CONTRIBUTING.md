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

## Large projects: EPIC → issues → pull requests

- For a substantial, multi-stage initiative, create **one parent tracking issue (EPIC)** with a concise goal, acceptance criteria, dependencies, and a checklist linking its child issues.
- Break the work into independently reviewable, testable child issues with clear scope. For implementation work, **each child issue gets its own branch and one dedicated PR**; retain the existing oldest-unfinished-work priority and main-sync rules. An EPIC does not override work queue order.
- Every child PR must include `Closes #<child-issue-number>`, pass relevant tests and review, and be merged before its checklist item is marked completed. Keep evidence in the child PR/issue. Never claim completion based only on an open PR or an unmerged branch.
- Keep the EPIC checklist synchronized with verified child issue status. Do not open an extra PR merely to complete or close the tracking EPIC.
- Close the EPIC only after all child issues are complete **and** the integrated solution satisfies the overall acceptance criteria. Explicitly distinguish merge from deployment; deploy only after separate authorization.
- Example: [AI Chess Arena UI redesign EPIC #304](https://github.com/MarVinPL/ai-chess-arena/issues/304), tracking issues #299–#303.

## One issue = one pull request

- Every implementation issue must have exactly one dedicated PR.
- Every such PR must close exactly one issue using `Closes #<issue-number>`.
- Never combine multiple issues in one PR.
- Never split one issue across multiple PRs.
- Do not add unrelated scope to an existing PR. Create a separate issue and a separate PR instead.
- Keep the issue/PR relationship explicit in the PR description and issue discussion.

## Canonical Issue identity and PR numbering

- The **Issue number is the canonical identifier of the task**. Use it consistently in the implementation branch, commit subjects, PR title/body, issue discussion, and work reports.
- Name implementation branches with the canonical Issue number, for example `feat/issue-57-short-description` or `docs/issue-57-short-description`.
- Every implementation PR must link to exactly one canonical Issue and include the exact closing keyword `Closes #<issue-number>` in its body.
- Include a direct link to the canonical Issue in the PR description when useful; do not rely on title similarity alone to establish the relationship.
- PR titles and commit subjects should include the canonical Issue number, for example `Issue #57: describe the change` and `issue-57: describe the change`.
- GitHub assigns Issue and PR numbers automatically from one repository-wide sequence. A separately created Issue and its PR therefore have different numbers; neither can be chosen or edited to force equality.
- In every status report, identify the pair explicitly as `Issue #N → PR #M`. Never label the PR number as the Issue number, or imply that two different GitHub objects share one number.
- Do not create fake aliases, renumber references, or try to reserve future numbers. If a task must have only one GitHub number, the PR itself can serve as the task record only after the repository owner explicitly changes the `One issue = one pull request` policy; do not make that policy change implicitly.

## Security review policy

- Security reviews and release-gate assessments for AI Chess Arena are performed internally by the implementation team, with documented evidence and an explicit disposition.
- **An external security audit or an independent external auditor is not a repository requirement or a release prerequisite.** Do not add either as a mandatory gate in Issues, PR checklists, deployment plans, or project documentation unless the repository owner explicitly changes this policy.
- Internal findings must be recorded as `PASS`, `FAIL`, or `NOT VERIFIED`, with the reviewed commit/scope, evidence references, severity, remediation, and accountable release-owner sign-off. Never report `PASS` without evidence.
- Internal review does not waive technical controls, artifact provenance/signing requirements, operational recovery tests, or explicit release-owner approvals that apply to a particular change.
- For Issue #157, the detailed implementation is defined in [`docs/deployment/issue-157-internal-audit-packet-2026-11-18.md`](docs/deployment/issue-157-internal-audit-packet-2026-11-18.md).

## Completion and release

- A task is not complete merely because code exists in a branch or on a server.
- Run relevant automated tests, syntax checks, and diff checks; review the final diff before merge.
- Merge is distinct from deployment. Do not deploy, restart services, modify databases, or change active tournament state without the required explicit approval.
- Report what was changed, tested, merged, and deployed separately. Never imply deployment from a successful merge.

## Tournament and runtime safety

- Treat active and planned tournaments, their rosters, pairings, and results as protected state.
- Do not change tournament state, model files, inference runtime, or database contents as part of unrelated repository work.
- Put follow-up behavior or additional improvements in their own issues and PRs.

## Event-driven VPS-to-main drift detection and controlled synchronization

- Do not run calendar-based weekly or other recurring drift checks.
- Perform a read-only comparison when it is relevant to project work: at the start of a work cycle involving the VPS, before making VPS changes, and after significant merges or deployments when the deployed revision may have become stale.
- Compare the deployed VPS revision with current `main`, including repository status, tracked and untracked changes, and the running service/release identity where available.
- Distinguish expected deployment lag from uncommitted VPS edits, a divergent checkout, and a deployment that was intentionally held back.
- When drift is detected, report the exact revisions, ahead/behind commits, affected paths, local modifications, operational risk, and whether an active tournament or inference process could be affected. Offer a controlled synchronization plan only after detecting drift.
- Detection is read-only. Never run automatic `git pull`, `reset`, `clean`, `stash`, checkout, deployment, migration, or service restart as part of monitoring.
- Before any approved synchronization, inspect and preserve all local edits and untracked files, confirm a usable backup/rollback path, and assess active tournament/runtime state. Never overwrite changes merely to make VPS match `main`.
- Synchronizing VPS code requires explicit user approval. Deployment, service restart, database migration, and tournament-state changes each require their own explicit approval.
- Repository changes that implement this workflow still follow the dedicated branch/PR process, and the PR must be synchronized with current `main` before merge.
