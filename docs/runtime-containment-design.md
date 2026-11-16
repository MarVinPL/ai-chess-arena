# Runtime containment design — Issue #157

Status: design note only. No host accounts, ACLs, service units, cgroups, network rules, or production settings were changed while preparing this note.

## Verified host facts (2026-11-16)

- Debian 13 host, kernel `6.12.111+deb13-cloud-amd64`, systemd `257.13`.
- Unified cgroup v2 exposes `cpuset`, `cpu`, `io`, `memory`, `pids` and additional controllers.
- The production API runs as `debian:debian`; its effective unit has `NoNewPrivileges=yes`, `PrivateTmp=yes`, `PrivateDevices=yes`, `ProtectSystem=full`, `ProtectHome=read-only`, but no `MemoryMax`, `CPUQuota`, `PrivateNetwork`, or separate runtime identity.
- The quarantine root `/var/lib/ai-chess-arena/hf-candidate-downloads` is `0700 debian:debian`; most job directories are also `0700`. A separate runtime UID cannot read the current artifacts without a deliberate handoff/permission design.
- The production service has write access to `/var/lib/ai-chess-arena`, which contains the SQLite DB and quarantine. Do not broaden this access for the runtime process.
- The host currently reports about 3.7 GiB RAM, 1.8 GiB available, and 874 MiB free swap; this is an observation, not a safe runtime budget.

## Required architecture before execution is wired

1. **Keep the API identity separate from the model identity.** Create a dedicated non-login runtime account with no supplementary groups, no home, no shell, and no access to the Arena database, secrets, SSH material, browser-vps files, or production release tree.
2. **Use a root-managed transient systemd service per job, not a child process owned only by the API.** The service must have a unique unit/cgroup name tied to the persisted job ID. Configure `User=`/`Group=`, `NoNewPrivileges=yes`, empty capability bounding set, `ProtectSystem=strict`, `ProtectHome=yes`, `PrivateTmp=yes`, `PrivateDevices=yes`, `ProtectKernelTunables=yes`, `ProtectKernelModules=yes`, `ProtectControlGroups=yes`, `ProtectProc=invisible`, `ProcSubset=pid`, `RestrictSUIDSGID=yes`, `LockPersonality=yes`, `MemoryDenyWriteExecute=yes` where compatible, `RestrictRealtime=yes`, `RestrictNamespaces=yes`, `SystemCallArchitectures=native`, and a reviewed syscall filter. Apply `MemoryMax=`, `MemorySwapMax=`, `CPUQuota=`, `TasksMax=`, `LimitNOFILE=`, `RuntimeMaxSec=`, and `KillMode=control-group` per job.
3. **Do not give the runtime process write access to the DB or quarantine.** It receives read-only access to exactly one verified artifact and a root-owned runtime bundle under `/usr/local/lib/ai-chess-arena-runtime`, plus a private writable output/temp directory under `/run/arena-runtime/job-<id>`. The bundle must contain the pinned `llamafile` binary and the reviewed worker entrypoint; both must be installed root-owned and non-writable by the API/runtime identities. The current worker and binary under `/home/debian` are deliberately inaccessible under `ProtectHome=yes` and must not be exposed by weakening that control. The API remains responsible for updating durable queue state.
4. **Artifact handoff must be race-safe.** The current quarantine is mode 0700 and mutable by `debian`; a separate UID cannot currently traverse it. Design a narrow read-only handoff (e.g. root-owned job snapshot or carefully managed per-file read-only ACL) and ensure the file cannot be replaced between hash verification and open by the worker. Do not recursively relax quarantine permissions.
5. **Network isolation needs a tested transport design.** `PrivateNetwork=yes` isolates loopback, so the current host-side HTTP health/inference client cannot reach a server bound to the worker's `127.0.0.1`. Do not simply disable `PrivateNetwork` and claim egress isolation. Preferred options to prototype are a Unix-domain-socket bridge inside the private network namespace, or a dedicated network namespace with explicit no-egress filtering and a narrowly exposed control channel. Prove the channel with integration tests before enabling it.
6. **Cgroup is the process ownership/recovery boundary.** Persist the transient unit name before asking systemd to start it. On cancellation, timeout, or API restart, stop only that exact unit and verify its cgroup is empty. This closes the current `Popen` → `register_process()` crash window more robustly than PID files alone. Never use broad `pkill`, process-name matching, or host-wide process cleanup.
7. **Fail closed on unsupported properties.** At unit creation, validate that all required systemd properties were accepted and are effective. If cgroup v2 controllers, memory limits, namespace controls, or the chosen network boundary are unavailable, mark the job blocked; do not silently fall back to an unsandboxed process.

## Resource policy still to decide

The host is small (2 vCPU / approximately 3.7 GiB RAM). A per-job `MemoryMax` and `MemorySwapMax` must be derived from live preflight readings and an explicit reserve for the API, database, OS, and other services. Do not use the full reported RAM/swap as the model budget. A candidate that cannot fit the conservative budget must become `skipped_blocked` without launching a process.

## Validation gates

- Unit inspection proves the runtime UID and all filesystem permissions are least-privilege; the runtime bundle is root-owned, and only the per-job artifact handoff plus bundle are read-only mounts.
- Negative tests prove no read/write access to DB, credentials, home directories, unrelated models, or host sockets.
- Network tests prove no outbound access while the one intended control channel works.
- cgroup tests prove memory/CPU/PID limits are enforced and all descendants disappear on cancellation, timeout, worker crash, and API restart.
- Crash injection at every boundary (persist unit name, start unit, worker exec, inference, stop unit) proves recovery never targets an unrelated process and never leaves an orphan runtime cgroup.
- The real-model E2E remains a separate, explicitly approved action. Until then use harmless synthetic child processes only.

## Current blocker summary

The existing adapter's process group and PID/start-time checks improve lifecycle ownership but do not provide a security sandbox. The runtime process is not yet wired to a worker. Do not mark Issue #157 complete or merge/deploy the executor until this containment boundary, recovery tests, and review are complete.

## Implemented supervisor orchestration prototype (2026-11-17)

The runtime_supervisor module now contains an injected-backend supervisor and a
SQLite unit-lease store. The lease store records the deterministic per-job unit
name in a transaction only after the corresponding queue row is already
running. The supervisor calls the durable persist callback before invoking the
backend start operation. If the start call fails ambiguously, the lease remains
recoverable as start_uncertain. Recovery reads only persisted incomplete
leases and asks the backend to stop each exact validated unit name; it never
enumerates processes or issues broad process-kill commands.

This remains an orchestration prototype, not an active supervisor:
- no backend implementation invokes systemd-run or D-Bus;
- no API/worker path constructs or calls this supervisor;
- the SQLite lease table is created only when the store is explicitly
  instantiated, and has not been added to the production database;
- no systemd unit, account, ACL, cgroup, network or production setting changed;
- backend stop() must still stop the exact unit and verify its cgroup is empty.

Tests use a fake backend and temporary SQLite databases, including persistence
failure, ambiguous start, exact-unit recovery, invalid unit names and
non-running queue rows. They do not start a systemd unit or a model process.
