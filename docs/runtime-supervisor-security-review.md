# Runtime supervisor security boundary review — Issue #157

Date: 2026-11-18  
Scope: source review plus disposable Debian 13 KVM validation of the Issue #157 supervisor, API-owned lease broker protocol, authorization gate, artifact handoff, IPC, and transient systemd policy. This is not an independent penetration test or production certification.

## Executive result

**Production deployment remains technically blocked.** The user has authorized a production rollout, but authorization does not close the remaining engineering release gates. The disposable VM demonstrated the real service lifecycle and several host-enforced boundaries; the remaining gaps still require provenance review and full integration/recovery coverage, followed by internal release-owner sign-off. No production files, services, accounts, groups, ACLs, sockets, database, or systemd configuration were changed during this review.

## Controls verified in source

- Supervisor IPC accepts only fixed actions and a positive integer job ID; peer UID is checked with Linux `SO_PEERCRED` before handler dispatch.
- The API lease broker accepts a strict four-action schema, rejects duplicate JSON keys, validates deterministic job-to-unit identity, bounds request/response sizes and timeouts, and authenticates only the configured peer UID. Production wiring fixes that UID to root and uses a `debian:debian` mode `0660` socket so the `root:debian` supervisor can connect without `CAP_DAC_OVERRIDE`.
- The privileged supervisor uses a broker client for lease writes and a separate SQLite `mode=ro`, `query_only=ON`, `trusted_schema=OFF` connection for authorization reads.
- Authorization rechecks the execution opt-in, persisted setting, running/non-cancelled job, approved unexpired batch, exact TEST_ONLY grant, artifact allowlist, verified artifact registry, fresh preflight, host reserves, and required manual review.
- Artifact handoff hashes while copying, uses directory file descriptors and `O_NOFOLLOW`, and retains handoff data while a durable lease is recoverable.
- The supervisor bundle and worker are required to be root-owned, non-group/world-writable, and SHA-256 pinned; the service requires Python `-I -S` and a canonical bundle path.
- The systemd backend rebuilds the complete unit policy from validated values, verifies effective properties after start, targets only deterministic per-job unit names, and checks cgroup-v2 population before confirming stop.
- Recovery is based on durable exact-unit leases, not process enumeration. Broker process `SIGKILL` recovery is covered by an isolated subprocess test using temporary SQLite/socket resources and a fake backend.
- The supervisor unit uses `Type=notify`; a disposable KVM guest confirmed that systemd reports readiness only after the AF_UNIX listener is active. Effective `ProtectSystem`, `ProtectHome`, network-family, capability, and writable-path settings were inspected in the guest.
- The disposable guest ran a real transient systemd worker unit and verified its effective properties and empty cgroup after stop. The worker was a harmless shell fixture, not a model runtime.
- SQLite WAL was exercised in the guest with a live writer and a separate read-only/query-only reader; reads succeeded and attempted writes were rejected.

## Findings / deployment blockers

### B1 — Production API dependency and shutdown ordering remain uninstalled

The repository now contains `ai-chess-arena-runtime-supervisor.service` and a production API drop-in example with `Requires=`/`After=`. The service unit passed `systemd-analyze verify` and ran successfully in the disposable KVM guest. The API drop-in remains an example and has not been installed or validated with the production API startup/shutdown sequence. Production API startup starts its broker and synchronously requests supervisor recovery before exposing the API.

**Status:** service artifact and supervisor readiness are validated. A host-side isolated broker test ran the broker as `debian:debian` and the client as `root:debian` with only `CAP_CHOWN CAP_DAC_READ_SEARCH`: socket mode `0660` passed, while `0600` correctly failed. `server.py` now configures `0660`. Production API dependency ordering and reverse shutdown behavior remain open.

**Required closure:** review the final API/supervisor/broker dependency graph, install the API drop-in only as part of the approved release, and validate API startup/restart/shutdown with the actual API process and DB in a disposable VM.

### B2 — SQLite WAL validation is partial

In the disposable Debian 13 guest, a live SQLite WAL writer committed data while a separate `mode=ro`, `query_only=ON`, `trusted_schema=OFF` connection read the committed row. A write through the read-only connection was rejected and the WAL sidecar was present. This validates the basic WAL/read-only behavior, but not the full supervisor service identity and sidecar matrix.

**Status:** basic WAL read/write separation passed in the VM; rollback-journal mode, absent/pre-existing `-wal`/`-shm` combinations, restart/recovery, and create/truncate/chmod denial across all relevant identities remain open. Granting broad write access to the DB directory remains prohibited.

### B3 — Systemd isolation validated in a disposable VM, but not with the production worker

A disposable Debian 13 KVM guest running systemd 257 and cgroup v2 successfully started the pinned supervisor zipapp as `Type=notify`, restarted it, and stopped it with clean socket removal. A real transient worker unit started and stopped; the backend verified effective unit properties and confirmed the exact cgroup was empty. Negative checks confirmed AF_INET was blocked under `RestrictAddressFamilies=AF_UNIX`, writes outside `ReadWritePaths` and writes under `ProtectHome=read-only` were denied, and an unrelated UID could not access the supervisor socket. `systemd-analyze verify` passed and `systemd-analyze security` reported exposure 4.1.

**Status:** real systemd/cgroup and several mount/network/DAC boundaries passed with a harmless shell worker fixture. The production worker binary, actual model mount, resource exhaustion/timeout behavior, process visibility, and full descendant-kill matrix remain open.

### B6 — Cleanup failed under the service's restricted capability set; fixed in this branch

The first VM recovery attempt exposed that the supervisor could not remove the runtime-owned output directory with only `CAP_CHOWN` and `CAP_DAC_READ_SEARCH`: mode `02710` intentionally withholds group write, and the unit does not grant `CAP_DAC_OVERRIDE`. The cleanup routine now validates the exact known entries first, then uses its already-granted `CAP_CHOWN` to transfer that exact directory to the supervisor and set mode `0700` before unlinking the known entries. It does not broaden the service capability set or perform recursive deletion.

**Status:** the updated bundle passed the restricted-capability recovery cleanup through the real supervisor IPC in the disposable VM. Unit coverage asserts the ownership/mode handback sequence. Keep this behavior in the internal audit scope.

### B4 — Artifact pins and release provenance remain manual provisioning inputs

The service checks root ownership, non-writable modes and SHA-256 pins, but the trustworthiness of the expected hashes depends on the separate release/provisioning procedure. The repository does not itself establish an authenticated approval/signature chain for the environment file and pin values.

**Required closure:** internal audit verifies how source commit, deterministic bundle, worker binary, hashes, allowlist, and root-owned environment file are approved and installed. Capture hashes from the release artifact; do not copy values from an untrusted runtime environment.

### B5 — Internal security audit and operational recovery drill remain outstanding

This is an internal audit criterion, not an external assurance claim. No real model was started and no production service or host resource was touched.

**Required closure:** the implementation team performs and records a structured internal audit of privilege boundaries, TOCTOU assumptions, SQLite broker transactions, artifact handoff, systemd policy, logging, and failure recovery. Record PASS/FAIL/NOT VERIFIED, evidence, severity, remediation, and final release-owner sign-off. Then run a documented disaster/restart drill in the existing isolated test environment, including API restart, supervisor restart, broker crash, cancellation races, and an unclean worker exit. The implementation team performs the review and records evidence; no external or independent auditor is a prerequisite.

## Provisioning checklist — only after separate approval

1. Freeze the exact release-owner-approved Git commit; build the deterministic supervisor zipapp and worker from approved sources; verify and record SHA-256 values.
2. Prepare a root-owned, non-writable bundle directory and root-owned environment file. Keep secrets and production acknowledgement out of Git.
3. Create dedicated non-login API/runtime identities only after checking existing UID/GID/group membership and collisions. Runtime identity must not share API identity.
4. Provision only the documented roots with exact owner/group/mode: handoff `root:debian 0711`, output `root:debian 0755` (the proposed service runs as `root:debian`), quarantine `API:API-group 0711`, supervisor socket directory `root:debian 0750`; validate every parent component and mount.
5. Pre-provision the lease table through the API-owned migration path. Do not let the privileged supervisor create or migrate schema.
6. Install the supervisor service unit only after B1–B4 have been reviewed; define API/supervisor ordering and readiness, and verify stop/restart ordering.
7. Run the disposable-VM SQLite/WAL and systemd sandbox matrices; retain logs, property snapshots, mount/cgroup evidence, and negative permission tests.
8. Complete the documented internal security audit and sign off each finding. Keep runtime execution opt-in disabled until all gates are closed.
9. Enable production execution only in a separately approved change window, with a rollback plan and a verified no-model/no-unit baseline.

## Explicit non-actions on the production host

- No production merge, deployment, production service restart, systemd unit installation, or runtime enablement.
- No production account/group/ACL/directory/socket/firewall/cgroup changes.
- No production DB access or schema migration.
- No model download or model execution. The only worker executed was a harmless fixture inside the disposable KVM guest.
- The disposable VM, its fake broker, SQLite database, worker fixture, and transient units were test-only; no test container or VM is left running.

## Evidence snapshot

- Branch: `feat/issue-157-runtime-executor-20261116`.
- Disposable guest: Debian 13 (trixie), systemd 257, cgroup v2, 1 GiB RAM, 2 vCPU; booted under KVM using a temporary disk image and removed after validation.
- Actual supervisor bundle SHA-256 in the VM after the cleanup fix: `7e00eb3c56ebd1470866cde1f26ccfc5499ee488145f474616726ba54663f861`.
- VM checks passed: Type=notify/readiness, service restart, SIGTERM/socket cleanup, API-UID status/recovery IPC, restricted-capability handoff cleanup, transient worker start/stop with empty cgroup, AF_UNIX allowed/AF_INET denied, ProtectSystem writable-path boundary, ProtectHome write denial, unrelated UID socket denial, and SQLite WAL read-only access/write denial.
- Isolated host broker group test: `debian:debian` broker socket `0660` accepted a `root:debian` client with only `CAP_CHOWN CAP_DAC_READ_SEARCH`; identical `0600` socket was denied. Temporary socket/service/files were removed.
- `systemd-analyze verify`: PASS. `systemd-analyze security`: exposure 4.1.
- Updated runtime suite: 716 passed, 1 skipped, 43 subtests passed; `compileall` and `git diff --check`: PASS before the documentation update.
- Production guardrails: `ai-chess-arena.service` active; supervisor unit not installed on production; no machines listed; production DB and configuration untouched.
- Remaining release gates: production API dependency/shutdown integration, full SQLite matrix, approved real-model artifact and separately authorized inference matrix, authenticated release provenance, and release-owner sign-off of the completed internal audit. No external/independent auditor is required.


### SQLite journal-mode boundary (2026-11-18)

The read-only supervisor identity was tested on the VPS with a disposable
database and the actual root:debian identity plus the service capability
bounding set (CAP_CHOWN, CAP_DAC_READ_SEARCH, with CAP_DAC_OVERRIDE and
CAP_FOWNER absent). Rollback-journal reads work alongside an API writer; WAL
reads work while -wal and -shm exist. A cleanly closed WAL database with both
sidecars absent cannot be opened read-only from the protected database
directory because SQLite attempts to create shared-memory state. The
supervisor must not be granted database-directory write access to mask this.
Keep the application on its default rollback-journal mode unless a separate
WAL lifecycle design is reviewed and validated.

### B3 — additional host-side systemd probes (2026-11-18)

The existing VPS was used for isolated transient-unit probes; no VM, container,
package installation, production unit change, or production artifact was used.
All test units were named `arena-b3-*-probe-20261118` and removed/reset after
validation.

- A disposable model fixture owned by the runtime test identity, mode `0400`,
  was bind-mounted read-only into a transient `nobody:nogroup` unit. The unit
  could read it and could not write it. The fixture and mount were removed.
- A live transient unit's effective properties were captured from systemd:
  `User=nobody`, `Group=nogroup`, `NoNewPrivileges=yes`,
  `ProtectSystem=strict`, `ProtectHome=read-only`, `PrivateTmp=yes`,
  `PrivateNetwork=yes`, the exact `BindReadOnlyPaths` model mount,
  `MemoryMax=67108864`, `MemorySwapMax=0`, `CPUQuotaPerSecUSec=500ms`,
  `TasksMax=4`, `RuntimeMaxUSec=30s`, and `KillMode=control-group`.
  Stop removed the exact transient cgroup.
- Memory exhaustion probe: a 128 MiB allocation under `MemoryMax=32M` ended
  with systemd result `oom-kill`; observed peak was exactly 32 MiB, swap 0.
- Timeout/descendant probe: a parent with a `sleep 300` child under
  `RuntimeMaxSec=3s`, `KillMode=control-group` ended with result `timeout`;
  the descendant PID was no longer alive and the transient cgroup was removed.
- A read-only bind mount probe and a model-fixture bind mount both passed.
  An initial attempt using a runtime-owned `/var/tmp` parent failed due to the
  host's mount/path visibility boundary; no permissions were broadened to
  work around it. The successful fixture used a root-owned `0755` parent and
  runtime-owned `0400` file.
- Added unit coverage for worker binary SHA-256 acceptance, hash mismatch
  rejection, and refusal of group/world-writable binary permissions.
  `tests/test_runtime_worker_supervised.py`: 12 passed.

**Remaining B3 limitation (rechecked 2026-11-18):** the pinned llamafile is
already present at `/home/debian/ai-agent/bin/llamafile` (43,856,395 bytes).
Its SHA-256 exactly matches
`756bf97febae557084e8ced9bde7a1cb0c7f6ce778530d582afa219406237a50`, and a
bounded `/bin/sh ... --version` probe returned `llamafile v0.10.6` (exit 0).
A second probe copied the exact-hash binary into a temporary root-owned,
read-only fixture under `/var/cache`, then executed it as `nobody:nogroup`
inside a transient systemd unit with `NoNewPrivileges=yes`,
`ProtectSystem=strict`, `ProtectHome=read-only`, `PrivateTmp=yes`,
`PrivateNetwork=yes`, `MemoryMax=256M`, `MemorySwapMax=0`, `CPUQuota=50%`,
`TasksMax=8`, and `RuntimeMaxSec=20s`. It returned `llamafile v0.10.6`
(exit 0). The temporary copy and unit were removed. An initial attempt from
`/run` failed because that tmpfs is mounted `noexec`; no mount flags were
changed. No download was necessary. However, the original file is owned by
`debian:debian` (mode 0755), while the new supervised runtime contract requires
a root-owned, non-group/world-writable binary under
`/usr/local/lib/ai-chess-arena-runtime/`. That runtime bundle and its worker
entrypoint are not installed on this VPS; `/run/arena-runtime` is also absent.
No GGUF model artifact is present in the inspected model locations, so no real
model inference or end-to-end worker-to-model run was performed. The pinned
binary identity/version is now verified, but bundle provenance/ownership and
real artifact execution remain open; B3 is not closed.

### B1 — additional existing-VPS integration test pass (2026-11-18)

The following focused integration tests were run on the existing Debian VPS
using the repository virtualenv; no production service, DB, socket, or systemd
unit was used by the tests:

- `tests/test_runtime_supervisor_broker_restart_integration.py`,
  `tests/test_runtime_api_worker.py`, and
  `tests/test_runtime_revocation_watchdog.py`: **26 passed**.
- `tests/test_runtime_api_supervisor_ipc_integration.py`,
  `tests/test_runtime_startup.py`,
  `tests/test_runtime_supervisor_service_notify.py`,
  `tests/test_runtime_supervisor_systemd_unit.py`, and
  `tests/test_runtime_worker_supervised.py`: **27 passed**.

These cover real AF_UNIX API-client/supervisor IPC, cancellation during a
blocked inference bridge, exact-job stop, broker restart/outage recovery,
SIGKILL after durable commit, startup recovery ordering/fail-closed behavior,
worker shutdown cancellation persistence, service readiness, and worker
systemd policy. Total focused result: **53 passed**.

This does not close B1: the actual production API process startup/restart/
shutdown sequence against a disposable database and broker/supervisor service
is not yet run end-to-end. The production API drop-in remains uninstalled.

### Full regression pass after the integration update (2026-11-18)

On the existing VPS, the complete repository suite completed with **728 passed,
1 skipped, and 43 subtests passed**. The first full-suite attempt had one
transient failure in `test_bridge_rejects_wrong_peer_uid`: the client observed
`BrokenPipeError` while the server rejected the unauthorised peer. The test
passed immediately when rerun in isolation, and the complete suite then passed
on a second run. No source change was made for this timing-sensitive result;
retain it as a flaky-test observation rather than claiming the first run was
clean.

The actual production API/supervisor end-to-end remains blocked by the absence
of the supervisor bundle, unit, dedicated identities, and runtime directories
on this host. Installing those prerequisites would be a deployment change,
not a harmless test. A fake supervisor IPC test is not equivalent to validating
systemd privilege separation and the production API shutdown ordering.
