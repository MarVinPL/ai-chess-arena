# Issue #157 — runtime API cutover plan (VPS snapshot 2026-11-18)

Status: **PREPARATION ONLY — production API not restarted; supervisor not installed; no runtime or model execution.**

This document supersedes assumptions in older deployment notes where they conflict with the live host snapshot below. It is not an execution authorization and is not a substitute for the Issue #157 release gates.

## Verified live state

Read-only inspection of the existing VPS found:

- `ai-chess-arena.service` is `active/running`; MainPID was `3417317` at inspection.
- The effective service starts `/home/debian/ai-chess-arena-live-ab4a8cb/server.py` with the shared venv at `/home/debian/ai-chess-arena/.venv/bin/python`.
- The running release is commit `ab4a8cb618e31f2345472f0c7002901ec0a104a1`.
- The release's `arena.sqlite3` symlink resolves to `/var/lib/ai-chess-arena/arena.sqlite3`. The DB is owned by `debian:debian`, mode `0600`; its parent is `debian:debian`, mode `0700`.
- The live DB uses SQLite `journal_mode=delete`; read-only checks returned `integrity_check=ok` and zero foreign-key violations.
- The DB contains 14 games; tournaments #5 and #6 are `completed`, and #7, #8, #9 are `planned` at the time of this snapshot. No tournament rows were changed by this inspection.
- `runtime_unit_leases` does not yet exist in the live DB.
- The active process has no `ARENA_DATABASE_PATH` in its environment. The symlink under the release directory currently directs its default DB path to the canonical `/var/lib` file.
- The installed API unit has no `RuntimeDirectory`; its effective `TimeoutStopSec` is 30 seconds. It has existing drop-ins, including the release selector and `/var/lib/ai-chess-arena` writable-path grant.
- `/etc/ai-chess-arena/runtime-process.env` is absent. The supervisor unit is not installed (`LoadState=not-found`).
- The version-controlled API unit on this branch already declares `RuntimeDirectory=ai-chess-arena-api`, mode `0750`, optional runtime process env, and `TimeoutStopSec=1960s`; these settings are not active on the host.
- `/run/ai-chess-arena-api` was not provisioned by the currently installed unit. No broker socket was found/started.
- The authenticated API state endpoint returned HTTP 401 during this inspection. Do not infer tournament runtime flags from that failed request; use the DB snapshot plus a properly authenticated read-only API check during the maintenance preflight.

## Important DB ownership and migration facts

`server.py` assigns the selected process DB to `league.DB` and the HF-download DB path. The production DB must therefore be explicitly pinned in the new service environment to the canonical path:

`ARENA_DATABASE_PATH=/var/lib/ai-chess-arena/arena.sqlite3`

Do not rely on a release-local symlink during the cutover, even though the current symlink resolves to the right file. Keep the existing `ReadWritePaths=/var/lib/ai-chess-arena` policy narrow; do not widen `ProtectHome` or grant the supervisor DB write access.

The API's production startup currently constructs `SQLiteRuntimeUnitStore(DB, initialize_schema=True)` after `db_init()` and before starting the broker. That constructor creates `runtime_unit_leases`. The privileged supervisor uses `initialize_schema=False` and must only read through the pre-provisioned schema; it must never create or migrate the application DB. The current `db_init()` alone does **not** create `runtime_unit_leases`.

Consequently, the cutover needs a separately tested, API-owned, idempotent schema-preparation step before the supervisor is allowed to recover leases. The implementation must use the selected canonical DB path and API identity, preserve all existing rows, and prove the schema shape. Do not hand-create the table with the root supervisor, and do not run a migration directly against production as an exploratory test.

## Required controlled sequence

1. **Release identity:** freeze the exact reviewed API commit and supervisor bundle SHA-256. Verify PR #173, all CI checks, systemd unit syntax on the VPS, and the complete release manifest. Do not deploy a moving branch name.
2. **Authenticated preflight:** use the existing approved session to read `/api/state`, `/api/live`, `/api/tournaments`, `/api/models`, and `/api/storage`. Require `running=false` and `tournament_running=false`; preserve every tournament/game row. If authenticated read-only state cannot be obtained, stop.
3. **Database backup:** create a SQLite online backup through the SQLite backup API while the live API is in a known quiescent state. Store it outside the live DB directory, record SHA-256, then verify `integrity_check`, `foreign_key_check`, tournament/game counts, and relevant status/pairing snapshots. A plain copy of the active DB is not acceptable evidence.
4. **Disposable migration proof:** restore the backup to a disposable DB on the same VPS. Run the exact API-owned lease-schema preparation against that copy. Verify table/column definitions, row preservation, integrity, FK checks, and repeat-run idempotence. No production DB writes during this step.
5. **WAL/rollback gate:** despite the current `delete` journal mode, test the actual backup/restore and supervisor read-only access behavior with the deployed SQLite/Python versions, including `-wal`/`-shm` sidecars and a writer closing/reopening. Document exactly how rollback restores the DB and any sidecars. Do not switch production journal mode as part of this work.
6. **Supervisor proof:** install nothing yet. On disposable state, validate supervisor bundle hash/ownership, `Type=notify` readiness, socket ownership/mode, `SO_PEERCRED`, lease recovery, stop/revoke behavior, cgroup identity, timeout behavior, and failure rollback. B1 requires a real API broker plus a real systemd supervisor, not mocked components.
7. **Systemd plan:** prepare a reviewed drop-in/unit transaction that adds API `RuntimeDirectory=ai-chess-arena-api` mode `0750`, `TimeoutStopSec=1960s`, the canonical `ARENA_DATABASE_PATH`, and the production-only `Requires=/After=` supervisor dependency. Preserve all existing auth env and storage isolation settings. Do not overwrite unrelated drop-ins.
8. **Production handover:** only after all release gates pass, perform one controlled maintenance window: capture fresh authenticated state and backup; install exact immutable artifacts/config; provision the API-owned schema; ensure supervisor socket readiness; hand over only the verified Arena PID/unit; start the API; verify PID/cgroup, broker socket, supervisor readiness, HTTP endpoints, DB integrity, and unchanged tournament/game snapshots. Never stop or restart SentinelX.
9. **Rollback:** if any check fails, stop only the Arena unit, preserve logs/artifacts, restore the exact pre-cutover API release and verified DB backup as a coordinated pair, verify integrity and state, then start the previous Arena release. Never attach the old process to SentinelX and never use broad process-kill commands.

## Explicit non-goals

- No runtime job, tournament, chess game, inference, or model is to be started as part of this API cutover.
- No model download, model-directory change, or shared model-directory access.
- No production DB migration until the disposable proof and rollback matrix pass.
- No `systemctl enable`/boot persistence change unless separately included in the approved release plan.
- No Caddy, SentinelX, ACL, or unrelated service changes.

## Outstanding release gates

- B1: real API + broker + systemd supervisor integration on disposable DB.
- B2: SQLite WAL/sidecar behavior and rollback proof.
- B3: reviewed and pinned worker artifact; model artifact approval and separate inference permission remain independent.
- B4: complete release provenance, hashes, environment pins, and approval chain.
- B5: independent security review and operational recovery drill.

A successful CI run or a passing unit test does not close these gates. The existing source review is not an independent security audit.
