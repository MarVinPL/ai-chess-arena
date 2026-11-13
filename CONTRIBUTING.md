# Contributing and repository workflow

## Work queue order

- Always start with the oldest unfinished item across **open pull requests and open issues**, ordered by creation time.
- Finish or explicitly resolve that item before moving to the next one. Do not skip an older PR to start a newer issue.
- At the start of each work cycle, inspect both open issues and open PRs; a PR may be the active implementation of an older issue.

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
