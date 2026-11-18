# Runtime supervisor security boundary review — Issue #157

Date: 2026-11-18  
Scope: static review of the Issue #157 supervisor, API-owned lease broker, authorization gate, artifact handoff, IPC, and transient systemd policy. This is an implementation review, not an independent penetration test or host-level certification.

## Executive result

**No production deployment approval.** The code has several strong fail-closed controls, but the remaining gaps are operationally significant and cannot be closed by unit tests alone. No production files, services, accounts, groups, ACLs, sockets, database, or systemd configuration were changed during this review.

## Controls verified in source

- Supervisor IPC accepts only fixed actions and a positive integer job ID; peer UID is checked with Linux `SO_PEERCRED` before handler dispatch.
- The API lease broker accepts a strict four-action schema, rejects duplicate JSON keys, validates deterministic job-to-unit identity, bounds request/response sizes and timeouts, and authenticates only the configured peer UID. The production wiring fixes that UID to root and the socket to mode `0600`.
- The privileged supervisor uses a broker client for lease writes and a separate SQLite `mode=ro`, `query_only=ON`, `trusted_schema=OFF` connection for authorization reads.
- Authorization rechecks the execution opt-in, persisted setting, running/non-cancelled job, approved unexpired batch, exact TEST_ONLY grant, artifact allowlist, verified artifact registry, fresh preflight, host reserves, and required manual review.
- Artifact handoff hashes while copying, uses directory file descriptors and `O_NOFOLLOW`, and retains handoff data while a durable lease is recoverable.
- The supervisor bundle and worker are required to be root-owned, non-group/world-writable, and SHA-256 pinned; the service requires Python `-I -S` and a canonical bundle path.
- The systemd backend rebuilds the complete unit policy from validated values, verifies effective properties after start, targets only deterministic per-job unit names, and checks cgroup-v2 population before confirming stop.
- Recovery is based on durable exact-unit leases, not process enumeration. Broker process `SIGKILL` recovery is covered by an isolated subprocess test using temporary SQLite/socket resources and a fake backend.

## Findings / deployment blockers

### B1 — No deployable supervisor service and dependency contract

The repository contains the API service unit but no `ai-chess-arena-runtime-supervisor.service` unit. The API production startup starts the broker and then synchronously requests supervisor recovery before exposing the API. The checked-in API unit has no `Requires=`/`After=` dependency on a supervisor unit. If production runtime mode were enabled before a supervisor listener and ordering contract existed, API startup would fail closed at recovery; this is an availability/deployment blocker, not permission to add a unit to the host.

**Required closure:** design and review a root-owned supervisor unit with explicit hardening, pinned zipapp `ExecStart`, root-owned environment file, `Restart=`/stop behavior, and a tested ordering/readiness contract. Verify broker readiness before supervisor recovery and ensure shutdown ordering does not remove the broker while supervisor callbacks remain possible.

### B2 — SQLite read-only/WAL behavior is not validated on the target service sandbox

Source uses read-only SQLite for authorization, but tests do not prove the effective file/sidecar access of the real API and root supervisor identities under the intended systemd sandbox while a WAL writer is active. SQLite WAL may require shared-memory coordination; the exact read-only behavior depends on SQLite version and pre-existing sidecars. Granting broad write access to the DB directory is explicitly prohibited.

**Required closure:** disposable-VM matrix for rollback journal and WAL, live API writer, pre-existing and absent `-wal`/`-shm` files, restart/recovery, permissions, and attempted write/create/truncate/chmod operations from the supervisor identity. Record SQLite version and effective access evidence. Do not test on production DB.

### B3 — Effective systemd isolation has only fake-runner coverage

Unit tests verify the generated command and compare mocked `systemctl show` properties. They do not demonstrate that the target host's systemd version accepts every property with the intended semantics, that mount isolation is effective, or that cgroup-v2 teardown behaves as assumed.

**Required closure:** disposable VM using the actual pinned worker and root-owned supervisor bundle; inspect `systemctl show`, mount namespace, network namespace, process/cgroup visibility, writable paths, resource limits, timeout kill, and descendant cleanup. A successful `systemd-run` submission alone is not sufficient.

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

## Explicit non-actions in this review

- No merge, deployment, service restart, systemd unit installation, or runtime enablement.
- No account/group/ACL/directory/socket/firewall/cgroup changes.
- No production DB access or schema migration.
- No model download, model execution, or real systemd runtime unit.

## Evidence snapshot

- Branch: `feat/issue-157-runtime-executor-20261116`.
- Prior runtime suite baseline: 708 passed, 1 skipped, 43 subtests passed; broker `SIGKILL` recovery test included.
- Host guardrails checked read-only: `ai-chess-arena.service` active; `machinectl list` empty; worktree clean before this review document was added.
