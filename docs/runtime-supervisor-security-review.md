# Runtime supervisor security boundary review — Issue #157

Date: 2026-11-18  
Scope: source review plus disposable Debian 13 KVM validation of the Issue #157 supervisor, API-owned lease broker protocol, authorization gate, artifact handoff, IPC, and transient systemd policy. This is not an independent penetration test or production certification.

## Executive result

**Production deployment remains technically blocked.** The user has authorized a production rollout, but authorization does not close the remaining engineering release gates. The disposable VM demonstrated the real service lifecycle and several host-enforced boundaries; the remaining gaps still require provenance review, full integration/recovery coverage, and independent security sign-off. No production files, services, accounts, groups, ACLs, sockets, database, or systemd configuration were changed during this review.

## Controls verified in source

- Supervisor IPC accepts only fixed actions and a positive integer job ID; peer UID is checked with Linux `SO_PEERCRED` before handler dispatch.
- The API lease broker accepts a strict four-action schema, rejects duplicate JSON keys, validates deterministic job-to-unit identity, bounds request/response sizes and timeouts, and authenticates only the configured peer UID. The production wiring fixes that UID to root and the socket to mode `0600`.
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

**Status:** service artifact and supervisor readiness are validated; production API dependency ordering and reverse shutdown behavior remain open.

**Required closure:** review the final API/supervisor/broker dependency graph, install the API drop-in only as part of the approved release, and validate API startup/restart/shutdown with the real API-owned broker in a disposable VM.

### B2 — SQLite WAL validation is partial

In the disposable Debian 13 guest, a live SQLite WAL writer committed data while a separate `mode=ro`, `query_only=ON`, `trusted_schema=OFF` connection read the committed row. A write through the read-only connection was rejected and the WAL sidecar was present. This validates the basic WAL/read-only behavior, but not the full supervisor service identity and sidecar matrix.

**Status:** basic WAL read/write separation passed in the VM; rollback-journal mode, absent/pre-existing `-wal`/`-shm` combinations, restart/recovery, and create/truncate/chmod denial across all relevant identities remain open. Granting broad write access to the DB directory remains prohibited.

### B3 — Systemd isolation validated in a disposable VM, but not with the production worker

A disposable Debian 13 KVM guest running systemd 257 and cgroup v2 successfully started the pinned supervisor zipapp as `Type=notify`, restarted it, and stopped it with clean socket removal. A real transient worker unit started and stopped; the backend verified effective unit properties and confirmed the exact cgroup was empty. Negative checks confirmed AF_INET was blocked under `RestrictAddressFamilies=AF_UNIX`, writes outside `ReadWritePaths` and writes under `ProtectHome=read-only` were denied, and an unrelated UID could not access the supervisor socket. `systemd-analyze verify` passed and `systemd-analyze security` reported exposure 4.1.

**Status:** real systemd/cgroup and several mount/network/DAC boundaries passed with a harmless shell worker fixture. The production worker binary, actual model mount, resource exhaustion/timeout behavior, process visibility, and full descendant-kill matrix remain open.

### B6 — Cleanup failed under the service's restricted capability set; fixed in this branch

The first VM recovery attempt exposed that the supervisor could not remove the runtime-owned output directory with only `CAP_CHOWN` and `CAP_DAC_READ_SEARCH`: mode `02710` intentionally withholds group write, and the unit does not grant `CAP_DAC_OVERRIDE`. The cleanup routine now validates the exact known entries first, then uses its already-granted `CAP_CHOWN` to transfer that exact directory to the supervisor and set mode `0700` before unlinking the known entries. It does not broaden the service capability set or perform recursive deletion.

**Status:** the updated bundle passed the restricted-capability recovery cleanup through the real supervisor IPC in the disposable VM. Unit coverage asserts the ownership/mode handback sequence. Keep this behavior in the independent review scope.

### B4 — Artifact pins and release provenance remain manual provisioning inputs

The service checks root ownership, non-writable modes and SHA-256 pins, but the trustworthiness of the expected hashes depends on the separate release/provisioning procedure. The repository does not itself establish an authenticated approval/signature chain for the environment file and pin values.

**Required closure:** independent review of how source commit, deterministic bundle, worker binary, hashes, allowlist, and root-owned environment file are approved and installed. Capture hashes from the reviewed release artifact; do not copy values from an untrusted runtime environment.

### B5 — Independent security review and operational recovery drill remain outstanding

The code review here is not independent assurance. No real model was started and no production service or host resource was touched.

**Required closure:** external reviewer examines privilege boundaries, TOCTOU assumptions, SQLite broker transactions, artifact handoff, systemd policy, logging, and failure recovery. Then run a documented disaster/restart drill in the disposable VM, including API restart, supervisor restart, broker crash, cancellation races, and an unclean worker exit.

## Provisioning checklist — only after separate approval

1. Freeze the exact reviewed Git commit; build the deterministic supervisor zipapp and worker from approved sources; independently verify and record SHA-256 values.
2. Prepare a root-owned, non-writable bundle directory and root-owned environment file. Keep secrets and production acknowledgement out of Git.
3. Create dedicated non-login API/runtime identities only after checking existing UID/GID/group membership and collisions. Runtime identity must not share API identity.
4. Provision only the documented roots with exact owner/group/mode: handoff `root:debian 0711`, output `root:debian 0755` (the proposed service runs as `root:debian`), quarantine `API:API-group 0711`, supervisor socket directory `root:debian 0750`; validate every parent component and mount.
5. Pre-provision the lease table through the API-owned migration path. Do not let the privileged supervisor create or migrate schema.
6. Install the supervisor service unit only after B1–B4 have been reviewed; define API/supervisor ordering and readiness, and verify stop/restart ordering.
7. Run the disposable-VM SQLite/WAL and systemd sandbox matrices; retain logs, property snapshots, mount/cgroup evidence, and negative permission tests.
8. Complete independent security review and sign off each finding. Keep runtime execution opt-in disabled until all gates are closed.
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
- `systemd-analyze verify`: PASS. `systemd-analyze security`: exposure 4.1.
- Updated runtime suite: 716 passed, 1 skipped, 43 subtests passed; `compileall` and `git diff --check`: PASS before the documentation update.
- Production guardrails: `ai-chess-arena.service` active; supervisor unit not installed on production; no machines listed; production DB and configuration untouched.
- Remaining release gates: production API dependency/shutdown integration, full SQLite matrix, production worker/real artifact mount and timeout matrix, authenticated release provenance, independent security review, and complete recovery drill.
