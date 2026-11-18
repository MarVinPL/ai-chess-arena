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

## Privileged runtime supervisor (Issue #157 — design only)

`ai-chess-arena-runtime-supervisor.service` is the proposed, separately
managed root-owned supervisor unit. It is **not installed or enabled** by this
repository change. It runs only the immutable zipapp at
`/usr/local/lib/ai-chess-arena-runtime/runtime_supervisor.pyz` using
`/usr/bin/python3 -I -S`; its required root-owned environment file is
`/etc/ai-chess-arena/runtime-supervisor.env` (mode `0600`, owner `root:root`).
That file must contain the exact production acknowledgement, canonical paths,
API/runtime identities, artifact allowlist, and independently reviewed SHA-256
pins. Never commit it or copy pins from an untrusted host process.

The supervisor uses `Type=notify`. The daemon sends `READY=1` only after its
AF_UNIX listener has bound, applied socket ownership/mode, and entered its
serving state. This is essential: `After=` with `Type=simple` or `Type=exec`
would not prove the socket is ready, and the API's one-shot recovery request
could race the listener. The watchdog may initially retry the API-owned lease
broker while the API is still starting; it fails closed and retries. The API
starts its broker before it sends the recovery request.

The supervisor unit declares `Before=ai-chess-arena.service`. The example API
drop-in `ai-chess-arena.service.d/20-runtime-supervisor.conf.example` adds
`Requires=` and `After=` so systemd starts the supervisor first and waits for
its readiness notification before launching the API. **Do not install this
drop-in in disabled or staging mode.** It is a production-only dependency
because the supervisor intentionally refuses to run without the explicit
production profile and its provisioned environment file. Without the drop-in,
the existing API service remains independent and unchanged.

The unit applies a restrictive filesystem view (`ProtectSystem=strict`,
`ProtectHome=read-only`), exposes only the handoff and runtime-output roots as
writable, limits address families to AF_UNIX, and bounds capabilities to the
file ownership/read-search operations required by the handoff implementation.
The root process retains access to the system manager through the local Unix
system bus; it does not need IP networking. The API broker socket remains
API-owned mode `0600`; root can connect to it. The supervisor socket lives in
the systemd-managed `/run/ai-chess-arena-runtime` directory, owned
`root:debian` mode `0750`, and is created `root:debian` mode `0660` for the API
peer UID.

### Required provisioning inputs and checks

- `/etc/ai-chess-arena/runtime-supervisor.env`: root:root `0600`; never in Git.
- `/usr/local/lib/ai-chess-arena-runtime/runtime_supervisor.pyz` and
  `runtime_worker`: root:root, no group/world write; hashes independently
  approved and matched to the env pins.
- `/var/lib/arena-handoff`: root:debian `0711`.
- `/run/arena-runtime`: root:debian `0755`, provisioned at boot by a separately
  reviewed tmpfiles or service dependency (not created by this unit).
- API quarantine root: pre-existing canonical API-owned directory with the
  exact identity/mode expected by `runtime_supervisor_service.py`.
- The DB path in the env file must be the canonical resolved database path;
  the supervisor receives read-only access through `ProtectHome=read-only`
  when the DB is under `/home`. A DB outside `/home` requires a reviewed,
  narrowly scoped read-only path policy; never grant supervisor write access
  to the DB or its SQLite sidecars.
- `RuntimeDirectoryGroup=debian` and the API unit's `User=debian`, `Group=debian`
  are an explicit current deployment assumption. If API identity changes,
  both service definitions and socket/group checks must be reviewed together.

Before installation, validate the unit against the target VPS's systemd
version, the exact effective mount namespace, socket readiness, the SQLite
rollback/WAL matrix, and the complete disposable-VM checklist in
`docs/runtime-supervisor-security-review.md`. `systemd-analyze verify` on a
workstation is syntax evidence only, not host compatibility or sandbox proof.

No service unit, drop-in, account, group, directory, socket, permission,
capability, systemd manager state, or production setting is installed or
changed by this design-only artifact.
