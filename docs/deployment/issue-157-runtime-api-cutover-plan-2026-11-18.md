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

- B1: **COMPLETE for the documented disposable API + broker + real systemd supervisor startup/recovery integration scope** (see evidence below).
- B2: **COMPLETE for the documented disposable SQLite online-backup, WAL/sidecar rollback/restore, and crash-recovery scope** (see evidence below).
- B3: **PARTIAL / OPEN** — reproducible worker/supervisor candidate bundles built and hashed; internal security review, approved/pinned runtime binary and model artifact, and separate inference permission remain outstanding.
- B4: **PARTIAL / OPEN** — source/build/hash traceability is recorded; authenticated signing identity, trusted verifier, explicit release-owner approval, and signed provenance chain are absent.
- B5: **internal, evidence-based security audit and operational recovery drill**. The implementation team may perform and document the audit; no external or independent auditor is a release prerequisite.

A successful CI run or a passing unit test does not close these gates. B5 is closed only by a documented internal audit with PASS/FAIL/NOT VERIFIED results, evidence references, remediation of blocking findings, and a completed disposable operational recovery drill.

## Disposable DB and broker proof (2026-11-18)

Completed on the existing VPS using a private disposable directory under `/var/tmp`; no production DB migration or API restart was performed.

- Created the disposable snapshot with Python SQLite's online `Connection.backup()` API from `/var/lib/ai-chess-arena/arena.sqlite3` while the production API remained active.
- Snapshot artifact: `arena-disposable.sqlite3`, SHA-256 `16183b65d7704622f6b0ce2bf274f01809edec0603db685448b74f3973482ac8`, size 7,782,400 bytes. The snapshot was kept in a mode-0700 temporary directory; the DB file was set to mode 0600.
- Before startup initialization, the snapshot had 14 games, 5 tournaments, 6,026 pairings, 57 league models, `integrity_check=ok`, zero foreign-key violations, and `journal_mode=delete`. The tournament statuses were #5/#6 completed and #7/#8/#9 planned; 14 pairings were completed and 6,012 pending.
- Ran the actual branch `server.db_init()` against the disposable DB with `ARENA_DATABASE_PATH` pinned to that file, followed by `SQLiteRuntimeUnitStore(..., initialize_schema=True)` and `runtime_executor.recover_orphaned_jobs()`. Repeated the same initialization sequence to check idempotence.
- Result: the runtime lease table and executor tables were created; the 14 games, 5 tournaments, 6,026 pairings, tournament statuses, and pairing status counts were preserved. Both integrity and foreign-key checks passed after the first and repeated initialization.
- Started the actual `RuntimeLeaseBrokerServer` on a private Unix socket against the disposable DB and exercised the real `RuntimeLeaseBrokerClient`: `persist`, `get`, `load_recoverable`, transitions to `active`, `stop_pending`, and `stopped`, then graceful broker shutdown. The socket was removed by the broker. One synthetic job and its stopped lease remain only in the disposable artifact.
- Separate WAL-sidecar test: cloned the disposable DB, enabled WAL only on that clone, held a reader transaction open to keep both `-wal` and `-shm` present, and exercised the real API-side SQLite lease store plus broker/client writes and reads. The lease round-trip passed while both sidecars existed; after closing readers and checkpointing, `integrity_check=ok`, no foreign-key errors, and the lease state was readable. This validates the broker/store path under a WAL sidecar scenario; it does **not** change or claim that production uses WAL.
- Production remained on the same active API process (PID `3417317` at final check), old release `ab4a8cb`, with `TimeoutStopSec=30s`; the supervisor unit remained not installed. The production DB remained in `delete` journal mode with no `-wal`/`-shm` sidecars at final inspection.

### What this proves — and what it does not

This closes the disposable API-schema/idempotence proof and provides a real Unix broker integration result on a disposable DB. It is useful evidence toward B1 and B2, but it does **not** close either gate: no actual systemd supervisor service was installed or started, no supervisor unit recovery/stop was exercised, and the WAL test does not establish a production cutover/rollback procedure. A coordinated backup-restore rollback drill and a real systemd supervisor integration test remain required.

## Real systemd supervisor gate — blocked by missing pinned worker bundle

A final read-only installation audit found that `/usr/local/lib/ai-chess-arena-runtime` does not exist; neither the pinned `runtime_supervisor.pyz` nor the `runtime_worker` executable is installed, and no built worker bundle exists in the checked project/agent directories. The repository does contain the worker source (`runtime_worker_supervised.py`) and its Python module dependencies; these have not yet been assembled, independently reviewed, or pinned as a deployable bundle. The supervisor service refuses to start unless both artifacts are root-owned, canonical, and match explicit SHA-256 pins. Its service configuration also binds the fixed production socket paths and canonical handoff/output roots.

I did not install a placeholder worker, create an unreviewed artifact, or start a fake supervisor: that would invalidate the artifact-provenance gate and could make a future runtime start execute the wrong program. Therefore the real `Type=notify` systemd supervisor integration test cannot safely run yet. This is a concrete B3 dependency and keeps B1 open. The remaining B1 test must use a reviewed, pinned worker bundle and supervisor zipapp on the disposable DB, with no model invocation and no production API handover.

## Worker bundle preparation and output-root contract correction (2026-11-18)

The worker source audit found and corrected a fail-closed configuration mismatch: the supervisor verifies `/run/arena-runtime` as `root:debian 0755`, and the live VPS directory has exactly those attributes, while the worker previously expected `root:root 0755`. The worker now resolves the configured supervisor group (`debian`) and verifies the exact root UID, group GID, mode, and canonical path. It also verifies its own executable via the fixed `sys.argv[0]` path, which is required for a zipapp entrypoint.

Added `tools/build_runtime_worker_bundle.py`, which produces a deterministic executable zipapp containing only the supervised worker entrypoint and its three standard-library-only project modules. It uses fixed ZIP timestamps, sorted members, a fixed interpreter shebang, and executable mode. The supervisor's existing `_verify_worker` checks the installed executable's root ownership, non-writable permissions, and exact SHA-256 before accepting requests.

A disposable candidate was built and invoked directly on the existing VPS. The no-argument invocation reached the worker argument parser and exited with the expected usage error (exit 2), proving the executable shebang and zipapp imports work. Candidate SHA-256: `d7d7ab4be3637bb9c618e1e6d1ec67cbfcd7d847b791b31d045b7d7486d6dc1d` (source state before final commit review). The candidate stayed under `/var/tmp`; it was not installed and no model/runtime process was launched.

Targeted tests covering the worker, deterministic supervisor bundle, supervisor service configuration/notify, systemd policy/backend and worker packaging: **72 passed**. Both versioned systemd units pass `systemd-analyze verify`; Python compile checks and `git diff --check` pass.

This is a packaging and source-level correction only. It does not close B1/B3/B4/B5 or authorize production installation. The final reviewed commit must be rebuilt to produce its own artifact hash, then pass the documented internal security audit and disposable real-systemd integration before installation.

## Supervisor zipapp reproducibility check

Rebuilt the existing deterministic supervisor zipapp from this branch on the VPS. It contains exactly the 14 expected source entries and passes ZIP integrity validation. Observed SHA-256: `7e00eb3c56ebd1470866cde1f26ccfc5499ee488145f474616726ba54663f861`. The candidate remains in a private `/var/tmp` directory and is not installed. The bootstrap deliberately requires its canonical production path, so invoking the temporary copy fails closed before service imports; the repository bundle tests separately verify dependency imports without a checkout.

This confirms reproducible packaging only. It is not evidence of a started supervisor daemon, and it does not close the real-systemd B1 integration gate or the remaining documented release gates.

## Disposable real-systemd recovery drill (2026-11-18)

Ran a bounded integration drill on the existing VPS using only disposable resources and no model process. A transient systemd unit named `ai-chess-arena-runtime-910000002.service` ran `/usr/bin/sleep 300` as `arena-runtime:arena-runtime` with `NoNewPrivileges`, empty capability sets, `ProtectSystem=strict`, `PrivateTmp`, `PrivateDevices`, `PrivateNetwork`, and `KillMode=control-group`. The unit was not installed persistently and was removed after the test.

A separate mode-0600 SQLite database under `/var/tmp` was initialized with the exact `runtime_unit_leases` schema and one synthetic `active` lease pointing to that exact transient unit. The actual `RuntimeSupervisor` + `SystemdTransientBackend` + `SQLiteRuntimeUnitStore(initialize_schema=False)` recovery path ran as root (the same effective UID required by the supervisor service):

- Before: one durable `active` lease.
- Recovery: exact unit stopped; count 1.
- After: no recoverable leases; persisted state `stopped`.
- systemd: `LoadState=not-found`, empty `ControlGroup`.
- SQLite: `integrity_check=ok`.

This is real-systemd + disposable-SQLite evidence for exact-unit stop and recovery-state transitions, beyond mocked backend tests. It is **not** a full B1 pass: it did not launch the pinned supervisor daemon/IPC service, did not exercise API startup readiness/recovery through the broker socket, and did not test worker execution. The production API and DB were not touched; no model was launched; no permanent systemd unit or bundle was installed.

## API-identity + broker + supervisor-IPC + real-systemd startup recovery (2026-11-18)

Extended the disposable recovery drill to exercise the actual cross-process trust boundaries and API startup ordering. No production paths, sockets, DBs, services, or model artifacts were used.

Test topology:

- Root supervisor process hosted the actual `RuntimeSupervisor`, `SystemdTransientBackend`, and `RuntimeSupervisorIPCServer`; the IPC socket allowed only the `debian` API UID and used group `debian`, mode `0660`.
- A separate child process dropped to the real `debian` UID/GID, created the API-owned `RuntimeLeaseBrokerServer` over a separate disposable SQLite DB, and called the actual `recover_runtime_on_startup()` path through `supervisor_request()`.
- The broker accepted only root (`SO_PEERCRED`) as its supervisor peer; the API child did not receive a direct privileged backend handle.
- The database was initialized on disposable storage with the API lease schema and a synthetic running job, then seeded with one `active` lease.
- A real transient systemd unit (`ai-chess-arena-runtime-910000008.service`) ran a harmless `/usr/bin/sleep 300` process as `arena-runtime:arena-runtime`, with no capabilities, `NoNewPrivileges`, `ProtectSystem=strict`, private tmp/devices/network, and `KillMode=control-group`.

Observed result:

- API startup recovery returned supervisor `count=1`, executor `count=0`.
- Supervisor stopped the exact unit through the real systemd backend.
- API-owned broker persisted the lease transition to `stopped`.
- Unit ended `LoadState=not-found`, `ActiveState=inactive`, empty `ControlGroup`.
- Disposable DB readback showed the expected `stopped` lease; `PRAGMA integrity_check=ok`.
- API broker and supervisor IPC listeners shut down cleanly; test resources were removed.

This proves the API-identity → supervisor IPC → root supervisor → API-owned lease broker → real systemd recovery path for a pre-existing lease, including startup ordering. It materially advances B1, but **does not fully close B1**: the pinned `runtime_supervisor.pyz` was not installed or launched as its actual `Type=notify` systemd daemon; `build_service()` production path verification, systemd READY notification, revocation watchdog, API's complete real `server.py` startup, and the real executor reconciliation callback remain to be tested together. No worker was installed or launched, no model inference occurred, and production remained untouched.

## Supervisor Type=notify preflight (2026-11-18)

Inspected the actual supervisor service entrypoint and unit. `runtime_supervisor_service.main()` starts the revocation watchdog, starts the IPC listener, waits for `server.ready`, and only then sends `READY=1` plus a status string through `_notify_systemd()`. The unit uses `Type=notify`, `NotifyAccess=main`, `TimeoutStartSec=30s`, and a 30-second stop timeout.

A real systemd manager smoke test ran the repository's `_notify_systemd()` implementation from a disposable transient `Type=notify` unit. systemd observed `ActiveState=active`, `SubState=running`, and the exact `StatusText`; the transient unit then stopped and was unloaded cleanly. This validates the host's notify transport and manager semantics, **not** the complete supervisor daemon readiness path.

The complete daemon cannot yet be started safely on this host without crossing into unapproved installation/provisioning: the immutable root-owned bundle is not installed at `/usr/local/lib/ai-chess-arena-runtime/runtime_supervisor.pyz`; the pinned worker is not installed; `/etc/ai-chess-arena/runtime-supervisor.env` and `/var/lib/ai-chess-arena/quarantine` are absent; and the required API broker socket directory `/run/ai-chess-arena-api` is absent. `build_service()` deliberately pins canonical production paths and rejects alternate staging paths. No canonical production paths were created and no artifacts, environment files, worker, or service unit were installed. Therefore B1 remains open pending an approved, fully isolated provisioning plan and full daemon + API integration run.

## Isolated supervisor harness design review (2026-11-18)

A deeper source review confirms that a test-only `build_service()` path cannot safely be implemented by merely relaxing its canonical path checks. The same production assumptions are independently enforced in multiple layers:

- `runtime_supervisor_service.build_service()` pins the supervisor bundle, worker, lease-broker socket, supervisor socket directory, handoff/output roots, root ownership, and exact modes.
- `RuntimeSupervisorHandlers._build_spec()` and the `start()` argv pin the production worker executable and runtime identity.
- `SystemdTransientBackend._validate()` independently pins the worker argv, runtime mount layout, read-only project path, and inaccessible handoff root. A caller-supplied alternate path would currently be rejected even if the service builder accepted it.
- `SocketHandoffSpec` verifies the output root as supervisor-owned `0755`; its job directory/socket policy is separately fixed.
- The artifact handoff module accepts injected roots, but still enforces owner/group/mode and canonical-path invariants at the filesystem boundary.

Therefore a safe isolated harness needs a coherent, explicit test profile propagated through the service builder, handlers, socket handoff, runtime-unit spec builder, and systemd backend validator. The test profile must be impossible to select from the production unit/environment and must not weaken production defaults. It must also use a distinct systemd unit namespace and a disposable database, broker socket, supervisor socket, handoff root, output root, quarantine root, bundle paths, and worker path. Its allowed paths should be constrained beneath one root-owned, non-symlink staging directory; all directories/files must be created with exact ownership/modes and removed after the drill. The test should run no worker and no model: only daemon readiness, IPC, API startup/recovery, and a synthetic harmless transient-unit stop/recovery are in scope.

The read-only host check reconfirmed: production API active at PID `3417317`; supervisor unit `LoadState=not-found`, `ActiveState=inactive`, `MainPID=0`; supervisor bundle, worker, env file, API broker runtime directory, and API quarantine directory all absent. No canonical production path was created. This review changes no runtime code and does not close B1. It prevents an unsafe partial implementation that would make the service accept staging paths while leaving the lower-level systemd validator pinned to production paths (or vice versa).

### Implementation sequence for the harness

1. Introduce an immutable runtime-path/profile object and pass it explicitly through the service, handlers, socket handoff, runtime-unit builder, and backend validator; retain current production constants as the only production profile.
2. Add a separate test-profile gate requiring an explicit test-only acknowledgement plus a dedicated staging-root marker; reject any profile whose paths escape the root or overlap production paths.
3. Give test transient units a separate, strict name prefix and allow only a harmless fixed executable in the harness. Do not make the test profile capable of launching the model worker.
4. Add tests proving production rejects every staging override, test profile rejects symlinks/path traversal/overlap/wrong ownership, and backend rejects production/test namespace crossover.
5. Build and root-pin temporary supervisor/worker test artifacts under the isolated root; launch the actual daemon as transient `Type=notify`, verify READY and IPC, then launch the actual API entrypoint against a disposable DB and isolated port/socket set.
6. Run recovery against one synthetic harmless transient unit, verify broker persistence, cgroup emptiness, API readiness/shutdown, SQLite integrity, and cleanup. Finally verify production PID, DB metadata, service units, and canonical paths are unchanged.

No staging-profile code has been added yet. The next implementation change must preserve this end-to-end boundary rather than bypass individual checks.

## Isolated `test-recovery` profile and full daemon/API recovery drill (2026-11-18)

Implemented a dedicated `test-recovery` deployment profile across the deployment resolver, supervisor service builder, API startup, lease broker, SQLite lease store, supervisor, and systemd backend. The profile is deliberately recovery-only:

- Requires `ARENA_RUNTIME_TEST_ACK=I_UNDERSTAND_RECOVERY_ONLY_TEST`, `ARENA_RUNTIME_BACKEND=systemd`, and `ARENA_RUNTIME_EXECUTION_ENABLED=0`.
- Requires one dedicated direct child of `/var/tmp`; every configured DB, socket, quarantine, handoff, output, bundle, log, and executor-lock path is derived from that root and checked against production-path overlap.
- Uses `ai-chess-arena-runtime-test-<job>.service`, a separate lease-broker namespace, and separate supervisor/backend namespace. Production defaults still accept only `ai-chess-arena-runtime-<job>.service`.
- Does not require, build, install, or launch a worker artifact. The IPC handler rejects every `start` request in this profile; the test daemon does not start the revocation watchdog. It can only report status and recover/stop a previously seeded exact test-namespace unit.
- API binds only `127.0.0.1` on port `0` (OS-assigned), uses a disposable SQLite DB and isolated log/lock paths, and requires background jobs off. The API runtime worker is not created because `production_requested` is false.
- The supervisor zipapp bootstrap accepts the test bundle only when the test acknowledgement, disabled execution flag, dedicated root, root ownership, immutable bundle path, and hash pin all match. Production bootstrap path/pinning remains unchanged.

The profile implementation also corrected `resolve_runtime_deployment()` to read `os.environ` when no explicit mapping is passed. Previously the default path silently resolved `{}`; server startup calls the resolver without an argument, so environment-based deployment selection would not have been observed. Explicit mapping-based tests remain supported.

### Real existing-VPS integration result

Executed on the existing VPS with a disposable online SQLite backup under `/var/tmp/ai-chess-arena-recovery-20261118-01`; no new VM/container was created. The test root was provisioned with separate ownership/modes for root supervisor files, API DB/logs/broker, quarantine, handoff, and output. No worker binary or model artifact was created or installed.

1. Built the actual supervisor zipapp from the current branch and pinned SHA-256 `4288196b7a517e0639f3732e6012f670a71568a2febbba178972f56b6c68339a` inside the disposable root.
2. Seeded the disposable DB with one synthetic active lease for `ai-chess-arena-runtime-test-910000001.service`.
3. Started a harmless `/usr/bin/sleep 300` transient unit under `arena-runtime` with no capabilities, `NoNewPrivileges`, strict filesystem protection, private tmp/devices/network, and `KillMode=control-group`.
4. Started the actual supervisor zipapp as a transient systemd `Type=notify` service with `python3 -I -S`. systemd observed `ActiveState=active`, `SubState=running`, and `StatusText=Supervisor IPC socket is accepting API peers`; the real supervisor IPC socket was root-owned `0660` in the isolated test root.
5. Started the actual branch `server.py` as `debian` with the test profile, disposable DB, loopback-only ephemeral HTTP port, isolated log directory and isolated executor lock path. API startup initialized its DB schema, started the API-owned broker, called supervisor startup recovery through IPC, ran executor orphan reconciliation, and reached HTTP readiness (`HTTP 200`).
6. Recovery stopped the exact synthetic systemd unit. The broker persisted the lease as `stopped`; systemd reported the test unit `LoadState=not-found`, `ActiveState=inactive`, empty `ControlGroup`; disposable DB `integrity_check=ok`, zero foreign-key violations.
7. An explicit IPC `start` request was rejected (`start_blocked=PASS`); no second unit was created. No worker or model process was launched.
8. API graceful shutdown removed its broker socket; stopping the transient supervisor removed its IPC socket. Both transient test units ended `LoadState=not-found`, `ActiveState=inactive`, `MainPID=0`.

Production safety verification after the drill: `ai-chess-arena.service` remained active with the same PID `3417317` and release `ab4a8cb`; the production supervisor remained `LoadState=not-found`, `ActiveState=inactive`, `MainPID=0`. No production DB migration, restart, worker installation, model download, inference, Caddy/ACL change, or persistent systemd unit was performed. The previous `/run/lock/ai-chess-arena-runtime-test.lock` file was only inspected; its timestamp remained 2026-11-17 23:54:42 and it was not changed. The new test profile uses its own lock path under the staging root.

**B1 is now satisfied for the defined API + broker + real `Type=notify` supervisor startup/recovery integration.** This does not authorize runtime worker execution or production cutover. B2 remains open for full WAL/sidecar rollback/restore; B3 remains open for reviewed/pinned worker artifact plus explicit model-artifact approval and separate inference permission; B4 remains open for complete provenance/approval chain; B5 remains open for the documented internal security audit and operational recovery drill. The disposable staging root is removed after evidence capture.

## SQLite WAL/sidecar backup and rollback drill — B2 evidence (2026-11-18)

Executed `tools/issue157_sqlite_rollback_drill.py` on the existing VPS with Python 3.13.5 / SQLite 3.46.1. The script opens the live source using SQLite URI `mode=ro`, takes its snapshot with `Connection.backup()`, and performs every mutation/restore under a new mode-0700 direct child of `/var/tmp`. It rejects a pre-existing staging root and never writes to the source DB.

Disposable root: `/var/tmp/ai-chess-arena-b2-rollback-20261118-04` (mode `0700`). The source `/var/lib/ai-chess-arena/arena.sqlite3` was in `journal_mode=delete`; the online snapshot passed `integrity_check=ok` and `foreign_key_check` with zero violations. The baseline artifact was 7,782,400 bytes, SHA-256 `5ce1f13b5128d6bc5fa3b6e8ff9e8c3489eea041ff4c3fc6ad783d0c56ef2a33`, with 14 games, 5 tournaments, and 57 league models. Its logical SQLite dump SHA-256 was `71a42dce1303ea528df36da60e711b812c07b4c79a5a90e5a34026b9a79f205d`.

### Executed matrix

1. **WAL sidecars present:** on a disposable clone only, enabled WAL, pinned a reader snapshot, committed a writer update, and observed both `-wal` (24,752 bytes) and `-shm` present.
2. **Backup includes committed WAL frames:** while both sidecars existed, made a second online backup. The backup contained the committed value `committed-before-backup`; integrity and FK checks passed. This proves the backup API captured committed WAL content rather than copying only the main DB file.
3. **Rollback with destination sidecars present:** committed a separate mutation to the disposable target and verified its logical dump differed from the restore point. Both sidecars were still present before restore. Restored with `Connection.backup()` into the same WAL-mode target; both sidecars remained SQLite-managed, the logical dump matched the restore point exactly (`d50ec91e6fb6e363fd2d00cc8eafa89b81739b8c07d16d34caa31133f97d22a0`), and the mutation-only row disappeared. `integrity_check=ok`, zero FK violations.
4. **Uncommitted writer termination:** a child process opened `BEGIN IMMEDIATE`, inserted an uncommitted probe row, signalled readiness, and was terminated with `SIGKILL` (exit `-9`). Reopening the disposable DB did not expose the uncommitted row; integrity and FK checks passed.
5. **Production invariants:** the production API remained active with MainPID `3417317`; the production DB remained `journal_mode=delete`, `integrity_check=ok`, zero FK violations, and no `-wal`/`-shm` sidecars. The production supervisor remained absent/inactive. No production DB write, API restart, journal-mode change, or service/configuration change occurred.

The machine-readable evidence was captured in `evidence.json`; SHA-256: `aefaad915e0247d4d967c23cf6cddc91fe19c20bbbeff1d73e7efc20ed58e35a`. The script and its tests are committed to the PR so the drill is reproducible; the private staging root is removed after evidence capture.

### Operational rollback rules established by the drill

- Take the rollback snapshot with SQLite's online backup API; never use a plain copy of a live DB file as the backup method.
- Quiesce/stop only the Arena API before a production restore and verify that no process still has the target DB open. Do not restore underneath active readers/writers.
- Restore through SQLite's backup API with a fresh connection. Do not manually copy, unlink, or synthesize `-wal`, `-shm`, or rollback-journal files; SQLite manages those files as part of its locking/recovery protocol.
- After restore, close all restore connections, reopen read-only, verify `integrity_check`, `foreign_key_check`, journal mode, schema/version, and application-level row/status snapshots before restarting the previous API release.
- A backup's logical contents do not imply the destination journal mode will change: the drill retained the destination's WAL mode. Therefore inspect and explicitly account for the target's journal mode; do not assume restoring a backup silently converts WAL to DELETE (or vice versa).
- Keep the DB backup and exact application release as a coordinated rollback pair. Never restore only one half of a schema-changing deployment.

**B2 is satisfied for the tested SQLite online-backup, committed-WAL/sidecar capture, sidecar-present rollback restore, and uncommitted-transaction crash-recovery scope on disposable copies.** This does not authorize production cutover or changing the production journal mode. B3, B4, and B5 remain open; B5 requires a documented internal security audit and a separately planned operational recovery exercise; no external auditor is required.

## B3 candidate artifact build and provenance preflight (2026-11-18)

Built the worker and supervisor zipapps twice from exact source commit `45b1c8bb16a8792d2f578624dc0aa2d71b7d7220` on the existing VPS. Both independent builds were byte-identical. ZIP CRC/integrity and exact member allowlists passed. The worker candidate's no-argument invocation reached the required `--job` parser and exited 2 before entering `run_job`; the supervisor candidate rejected a missing deployment profile/acknowledgement before importing or starting service modules. No worker process, llamafile binary, model artifact, or inference was started.

Candidate SHA-256 values:

- `runtime_worker`: `d7d7ab4be3637bb9c618e1e6d1ec67cbfcd7d847b791b31d045b7d7486d6dc1d` (9,574 bytes; ZIP contains only `__main__.py` plus the three reviewed standard-library project modules).
- `runtime_supervisor.pyz`: `bddd166dc46d14f2dcb6d08ac7acfd41e7e46e8c023a51c343719a0f19bbeb02` (44,746 bytes; ZIP contains the bootstrap and the 13 allowlisted supervisor modules).

Build environment: Python 3.13.5, zlib 1.3.1. The source tree was clean at build time; source commit/tree, SHA-256 for every included source/builder file, artifact hashes, sizes, modes, ZIP members, and inert smoke-test outcomes are recorded in `docs/deployment/issue-157-artifact-manifest-2026-11-18.json`.

Candidates remained under `/var/tmp/ai-chess-arena-b3-artifacts-20261118-01`, owned by `debian:debian`; they were not installed into `/usr/local`, not root-pinned, and not referenced by production configuration. The worker source still pins a specific llamafile SHA-256, but the corresponding binary has not been supplied/reviewed/approved as part of this candidate build. There is no model artifact approval and no inference permission. Consequently this is reproducible packaging/provenance evidence only: **B3 remains OPEN** pending completion of the internal security audit, a reviewed/pinned runtime binary and model artifact, and the separately required explicit inference authorization. B4 also remains open for the complete approval/signature chain; B5 remains open for the documented internal security audit and operational recovery.

## B3 read-only runtime artifact cross-check (2026-11-18)

A further read-only cross-check compared the worker's compiled-in `LLAMAFILE_SHA256` with the already-present legacy binary `/home/debian/ai-agent/bin/llamafile`. The SHA-256 matches exactly: `756bf97febae557084e8ced9bde7a1cb0c7f6ce778530d582afa219406237a50` (43,856,395 bytes). The file is currently `debian:debian`, mode `0755`, and its file signature is a DOS/MBR-style shell-polyglot prefix (`4d 5a 71 46`), consistent with the worker's `/bin/sh` dispatch branch. This is a hash match only; no binary was executed and no upstream provenance/signature/licence review was performed.

**The existing binary is not an installable runtime candidate in its current state.** The worker's `_verify_binary()` requires UID 0 ownership, no group/world write bits, and the exact hash. The canonical `/usr/local/lib/ai-chess-arena-runtime/llamafile` is absent; the candidate runtime directory and worker/supervisor systemd unit files are also absent. The legacy binary's `debian:debian` ownership therefore fails the worker's root-owned binary contract. It was not copied, chowned, chmodded, or otherwise modified.

Static source review confirmed the intended trust chain is artifact-specific: enqueue checks a TEST_ONLY permission against model ID/repository/full 40-character revision/SHA-256; supervisor authority rechecks the same grant and verified quarantine registry; handoff copies the expected size/hash into a per-job immutable snapshot; the worker checks the read-only model file's owner/mode/size and checks the llamafile's root ownership/mode/hash. This was a limited source-level review, not the complete documented internal security audit.

No exact GGUF model repository, revision, artifact SHA-256, filename, or licence has been selected/approved for this runtime release. No model was downloaded or copied. There is no active TEST_ONLY grant for a concrete artifact in this B3 candidate record, and no inference authorization. This is consistent with the user's earlier explicit constraint that proceeding with PR review does not approve model download, execution, or inference. **B3 remains OPEN.** Do not promote the legacy binary by changing ownership or permissions: a future install needs verifiable binary provenance, a root-owned immutable bundle, a separately reviewed model artifact and licence, and the required explicit user approvals.

## B4 release-provenance and signing-infrastructure inventory (2026-11-18)

The current evidence chain now records the source commit/tree, per-source SHA-256 values, two-build byte reproducibility, worker/supervisor candidate hashes, build Python/zlib versions, ZIP member allowlists, inert smoke-test results, and the read-only legacy llamafile hash cross-check. The full regression suite and CI result are also tied to the PR head. This is useful traceability, but it is not an authenticated release attestation.

A read-only repository/host inventory found no existing `cosign`, `gpgv`, or `in-toto-run` executable, no repository signing/attestation workflow, and no documented release-signing key or trust-root policy. `gpg` and `openssl` executables exist, but their presence is not a signing identity or an approval chain. No key was generated, imported, inspected, or used; no self-signed or locally trusted signature was fabricated. The current JSON manifest is deliberately unsigned.

Required B4 closure still needs an agreed trusted signing identity and verifier/trust-root distribution, authenticated approval of the exact source commit and worker/supervisor/binary/model hashes, a signed provenance/attestation artifact, and a root-owned environment/pin file whose installation is verified against that approved record. The release owner must explicitly approve the exact source commit and artifact hashes through the documented authenticated approval process; a CI pass alone is not approval. **B4 remains OPEN (traceability evidence present; authenticated approval/signature chain absent).**

The repository contains a separate SmolLM2-1.7B GGUF candidate record (`docs/model-audit/candidate-issue-132-smollm2-1.7b.md`), but its status is `review_required`, `not_admitted`, and `not technically evaluated`; it is not a runtime model selection or approval. Its presence does not authorize download, handoff, execution, or inference.
