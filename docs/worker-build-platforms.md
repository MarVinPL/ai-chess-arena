# Worker application builds — temporary desktop pause

**Decision (2026-11-22, Issue #225):** Build AI Chess Arena Worker for **Android only** until the user explicitly requests Linux or Windows again.

## Current build policy

| Target | Status | Reason |
| --- | --- | --- |
| Android ARM64 | **Enabled** | The active platform; retain code-change-only builds and the existing debug APK validation |
| Linux | **Paused** | Defer future builds until there is a concrete need |
| Windows | **Paused** | Defer future builds until there is a concrete need |

The complete Linux and Windows build jobs remain in `.github/workflows/worker-multiplatform.yml`, but are protected by always-false job conditions (Issue #225). Changes confined to Linux/Windows-specific sources no longer trigger the workflow at all. Shared Worker source changes can still trigger the *Android* build; changes confined to CI YAML, docs or tests do not build any application.

**Nothing is deleted:** retain desktop source code, platform-specific icons, build job steps, existing GitHub Actions artifacts and Git history. Previously produced desktop artifacts continue to follow their existing GitHub retention periods, so they are not guaranteed to be available indefinitely. Do not delete or reset APK signing or Android Keystore keys.

## How to resume desktop builds

Create a new GitHub Issue and a separate PR; do not push changes directly to `main`.

1. In `.github/workflows/worker-multiplatform.yml`, change `if: ${{ false && needs.detect.outputs.linux == 'true' }}` back to `if: needs.detect.outputs.linux == 'true'` when re-enabling Linux; do the equivalent for Windows only when needed.
2. Reinstate its specific `on.push.paths` and `on.pull_request.paths` entries (e.g., `remote_worker_gui/src-tauri/gen/linux/**`, `remote_worker_gui/src-tauri/linux/**` or their Windows equivalents). Shared-source triggers remain enabled.
3. Keep the existing per-platform `dorny/paths-filter` rules and ensure no rebuild occurs on workflow-only changes.
4. Test only the platform(s) re-enabled and inspect the newest successful artifact before any release or distribution.

**Important for open stacked PRs (e.g., #216):** Those may contain their own older copy of `.github/workflows/worker-multiplatform.yml`; synchronize the pause into those branches via their own PR before any merge that would otherwise restore Linux/Windows builds. Do not interrupt Android-only jobs merely to edit CI.

See [Issue #225](https://github.com/MarVinPL/ai-chess-arena/issues/225).
