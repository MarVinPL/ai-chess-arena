# Dedicated systemd service

ai-chess-arena.service is the version-controlled service definition for the
Arena API. It is intentionally separate from sentinelx-cloud-core.service.

## Runtime contract

- Runs as debian:debian from /home/debian/ai-chess-arena.
- Starts the repository virtualenv Python and server.py.
- Provisions the API-owned `/run/ai-chess-arena-api` runtime directory with
  mode `0750`; the runtime lease broker socket is created there with mode
  `0600` and is accessible only to the API identity and privileged root
  supervisor. This directory is lifecycle-managed by systemd and is not
  created by repository code.
- The current application binds the API to 127.0.0.1:8090.
- ProtectHome=read-only keeps /home read-only by default. BindPaths exposes
  only /home/debian/ai-chess-arena as a writable bind mount, allowing SQLite,
  logs, and Arena-owned model files to be updated.
- InaccessiblePaths hides /home/debian/ai-agent/models entirely.
- The llamafile executable is exposed read-only at
  /home/debian/ai-agent/bin/llamafile.
- KillMode=control-group ensures child processes remain in the service
  lifecycle. Do not use SentinelX's service as a restart mechanism.

## Installation gate

Deployment status (2026-11-14): this unit is installed at
/etc/systemd/system/ai-chess-arena.service and currently active. It is not
enabled for boot persistence (UnitFileState=disabled); enabling it requires
an explicit operational decision. The service is independent of SentinelX.

Before installing or starting it:
1. Obtain separate, explicit approval for the deployment and restart.
2. Follow the VPS deployment/rollback runbook in
   docs/deployment/vps-controlled-deployment-runbook-2026-11-13.md.
3. Back up and validate the production database and application files.
4. Arrange a controlled handover of the existing API process. The current
   process is not owned by an ai-chess-arena.service unit and shares the
   sentinelx-cloud-core.service cgroup. Do not restart or stop SentinelX.
5. Confirm port 8090 is free before starting this service. Do not run two API
   processes against the same database.

Only after those gates are met should an operator install the unit, reload
systemd, and start/enable ai-chess-arena.service.

## Validation

Offline validation:
- systemd-analyze verify deploy/systemd/ai-chess-arena.service
- python -m pytest -q tests/test_systemd_unit.py

No systemd state is changed by either validation command.

## Rollback

Rollback requires separate approval. Stop only ai-chess-arena.service, restore
the application/database snapshots according to the deployment runbook, and
verify the API and SQLite integrity before any tournament is started. Never
stop or restart sentinelx-cloud-core.service as part of Arena rollback.

## SQLite database path and rollback journal

SQLite in the default `DELETE` journal mode creates its rollback journal beside
the resolved database file. Granting `ReadWritePaths` only to a database file
or to a symlink in the release worktree is insufficient when that symlink
points into a different persistent directory. The service must have a
`ReadWritePaths` entry covering the **resolved parent directory** of the
database file. Prefer a dedicated data directory (for example,
`/home/debian/ai-chess-arena-data`) that contains only the Arena database and
its SQLite sidecar files. Do not grant write access to an old release worktree
or to all of `/home/debian` merely to let SQLite create its journal. The
`zz-arena-sqlite-storage.conf.example` file shows the narrow systemd drop-in
entry; it is only a template and is not installed automatically.

Before deployment, resolve the configured database path and compare its parent
with the effective unit `ReadWritePaths` values. The helper below checks the
symlink target rather than the symlink's location:

```sh
DB_PATH=/home/debian/ai-chess-arena-live-46713c0/arena.sqlite3
mapfile -t RW_PATHS < <(systemctl show ai-chess-arena.service -p ReadWritePaths --value | tr ' ' '\n')
ARGS=()
for path in "${RW_PATHS[@]}"; do
  [ -n "$path" ] && ARGS+=(--read-write-path "$path")
done
python3 deploy/systemd/check_sqlite_writable_path.py --database "$DB_PATH" "${ARGS[@]}"
```

A failing check means the database must be placed in a dedicated persistent
data directory and that directory added to a reviewed drop-in before a
database-writing feature is used. Moving the live database and changing its
symlink require a separate, backed-up, approved maintenance operation. Do not
weaken
`ProtectHome=read-only`, `ProtectSystem=full`, or the model-directory isolation
to work around this. Installing a drop-in, reloading systemd, and restarting
the service remain separate, explicitly approved operational steps.
