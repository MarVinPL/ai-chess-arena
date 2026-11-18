# Issue #157 — internal security audit packet (2026-11-18)

Status: **internal audit evidence complete for the documented B5 scope; release-owner sign-off pending**. The implementation team may perform the audit. No external or independent auditor is a release prerequisite. This packet is not by itself a security certification, release approval, or authorization to install or execute runtime artifacts.

## Scope and current disposition

| Gate | Current status | Evidence / remaining requirement |
|---|---|---|
| B1 — API, broker, real systemd supervisor startup/recovery | **COMPLETE for the documented disposable integration scope** | See the B1 evidence section in `issue-157-runtime-api-cutover-plan-2026-11-18.md`. Does not authorize worker execution or production cutover. |
| B2 — SQLite WAL/sidecar backup and rollback | **COMPLETE for the documented disposable-copy scope** | See the B2 evidence section in the cutover plan. Production DB/journal mode were not changed. |
| B3 — runtime artifacts and model admission | **OPEN — CANDIDATE METADATA PINNED; LICENSE/ARTIFACT ADMISSION PENDING** | Exact Light-R1 repository/revision/file/upstream LFS SHA-256/size are now recorded from read-only Hugging Face API metadata. The repository/card declare Apache-2.0, but no LICENSE/COPYING file exists, so manual license review remains pending and `verified_permissive` stays false. Root-owned worker/binary verification passed only in the isolated container; host runtime installation, quarantined artifact, TEST_ONLY grant and inference authorization remain absent. No weights were downloaded. See `issue-157-model-candidate-metadata-2026-11-18.json`. |
| B4 — authenticated provenance and signing | **OPEN — GOVERNANCE + WORKFLOW IMPLEMENTED; LIVE ATTESTATION/VERIFICATION PENDING** | Protected-main CI and the `runtime-release` required-reviewer environment are configured. The main-only workflow precomputes candidate digests, gates attestation on owner approval, rebuilds and compares exact digests, and emits worker/supervisor plus approval-statement attestations. The VPS verifier now authenticates the approval statement before checking its exact identity/scope/digest bindings; negative policy tests pass. No main-ref run exists yet; live VPS cryptographic verification and deployment-review actor cross-check remain unverified. |
| B5 — internal security audit and operational recovery drill | **TECHNICAL PASS — OWNER SIGN-OFF PENDING** | IA-01–IA-10 source/test review, isolated API/broker/supervisor crash/restart, and a real `runtime_worker` SIGKILL after integrity checks but before model subprocess creation are evidenced. Exact lease recovery and empty cgroup verified. No model or inference was run. Final release-owner acceptance remains pending. No external/independent auditor is required. |

## Evidence index

1. `issue-157-runtime-api-cutover-plan-2026-11-18.md` — scope, B1/B2 evidence, B3 artifact preflight, B4 trust-gap inventory, and explicit limitations.
2. `issue-157-artifact-manifest-2026-11-18.json` — runtime candidate artifact hashes, sizes, modes, source commit/tree, reproducible-build result, ZIP member allowlists, and smoke-test outcomes.
3. `issue-157-model-candidate-metadata-2026-11-18.json` — metadata-only pin of the Light-R1 GGUF revision, filename, upstream LFS SHA-256/size, and still-pending license/admission state.
4. PR #173 — implementation and review history: https://github.com/MarVinPL/ai-chess-arena/pull/173
5. Issue #157 — requested behavior and safety constraints: https://github.com/MarVinPL/ai-chess-arena/issues/157
6. `issue-157-container-crash-recovery-evidence-2026-11-18.json` — machine-readable evidence for the isolated-container API/broker/supervisor SIGKILL/restart matrix and production invariants.
7. `issue-157-worker-crash-evidence-2026-11-18.json` — machine-readable evidence for the real supervised worker SIGKILL and exact-lease recovery drill.

## Candidate artifact identifiers (not approved for installation)

- Source commit used for the recorded candidate build: `45b1c8bb16a8792d2f578624dc0aa2d71b7d7220`
- `runtime_worker` SHA-256: `d7d7ab4be3637bb9c618e1e6d1ec67cbfcd7d847b791b31d045b7d7486d6dc1d` (9,574 bytes)
- `runtime_supervisor.pyz` SHA-256: `bddd166dc46d14f2dcb6d08ac7acfd41e7e46e8c023a51c343719a0f19bbeb02` (44,746 bytes)
- Two independent builds were byte-identical. This establishes reproducibility for the recorded build process, not trusted provenance or source approval.
- Official Mozilla AI release `0.10.6`, asset `llamafile-0.10.6-thin`: GitHub release API digest and size match the worker pin exactly (`756bf97febae557084e8ced9bde7a1cb0c7f6ce778530d582afa219406237a50`, 43,856,395 bytes); a fresh HTTPS download was rehashed on the VPS and matched.
- A separate root-owned `0555` staging copy at `/var/tmp/ai-chess-arena-runtime-provenance-20261118/llamafile` passed the worker's actual `_verify_binary()` check. The binary was not executed. The legacy `/home/debian/ai-agent/bin/llamafile` remains unchanged; its ACL grants runtime read/execute but it remains `debian:debian`.
- This validates the staged binary against the published release digest, not a cryptographic signature or authenticated approval chain. Canonical bundle installation, model admission and inference authorization remain outstanding; B3/B4 stay open.

## Internal audit checklist

The audit operator should record identity, audit date, exact commit and artifact hashes reviewed, tools/versions, evidence examined, findings with severity, and a signed disposition.

### Security and implementation

- [ ] Validate authorization boundaries: TEST_ONLY grant binding to exact model ID/repository/revision/hash, expiry/revocation, and fail-closed behavior.
- [ ] Review quarantine registry trust, artifact handoff/snapshot immutability, path traversal/symlink handling, ownership/mode checks, and TOCTOU resistance.
- [ ] Review supervisor IPC authentication, Unix peer credentials, socket permissions, lease ownership, replay/stale-message handling, and API/supervisor trust boundaries.
- [ ] Review systemd unit hardening, cgroup/process identity, child-process termination, timeout/cancellation, resource limits, and cleanup after crashes.
- [ ] Review SQLite transaction boundaries, idempotency, WAL/SHM recovery assumptions, and rollback failure modes.
- [ ] Review logs and panel output for secrets, untrusted model metadata, misleading progress, and accidental roster/allowlist mutation.
- [ ] Confirm no path can start a worker/model without all required safety gates and the explicit per-run authorization required by policy.

### Provenance and artifact admission

- [ ] Define the signing identity approved by the release owner and the verifier/trust-root distribution; do not treat a key created solely by the build operator as an authenticated approval chain.
- [ ] Authenticate approval of the exact source commit/tree and worker, supervisor, runtime-binary, and model hashes.
- [ ] Verify signed build provenance/attestation and its builder identity, inputs, environment, and reproducibility claims.
- [ ] Establish runtime-binary origin from verifiable provenance and satisfy the root-owned immutable install contract.
- [ ] Select and review one exact model artifact, including repository, immutable revision, filename, SHA-256, licence, and admission decision. No model download is implied by this checklist.

### Operational recovery drill (separate from code review)

- [ ] Approve a written, isolated test plan with disposable state and explicit stop/rollback criteria.
- [ ] Exercise supervisor/API startup, readiness, lease reconciliation, revoke/stop, timeout, crash recovery, and rollback on the existing VPS only.
- [ ] Verify that only the test-owned process/cgroup is affected and that production API, DB, Caddy, ACLs, and unrelated services remain unchanged.
- [ ] Capture command output, timestamps, service/process identities, DB integrity evidence, and before/after production health checks.
- [ ] Remove disposable state and verify no transient units, test processes, or staging artifacts remain.

## Hard stop conditions

- No production API restart or handover, production DB migration/journal-mode change, canonical runtime installation, permanent supervisor/worker service installation, or model inference under this PR's current authorization.
- Do not change ownership or permissions of the legacy llamafile to force it through the worker check.
- Do not download, copy, activate, or execute a model artifact without a separate explicit approval for the exact artifact and action.
- Do not mark B3/B4/B5 complete based on this packet, CI success, reproducible builds, or hash matches alone; execute and document the required checks and obtain explicit release-owner approval.
- Keep PR #173 in Draft and do not merge until the internal audit, artifact/provenance approvals, and release gates are evidenced and separately authorized.

## Internal audit disposition (to be completed by the audit operator)

- Audit operator:
- Audit scope and limitations:
- Reviewed commit:
- Reviewed artifact hashes:
- Findings and severity:
- Required remediation:
- Operational drill evidence reference:
- Decision: `APPROVE` / `APPROVE WITH CONDITIONS` / `REJECT` / `MORE EVIDENCE REQUIRED`
- Audit record / authenticated release-owner approval reference:
- Date:

## Internal audit execution log — 2026-11-18 (in progress)

- Audit operator: implementation-team internal review (assisted source/test inspection).
- Reviewed commit at start: `a3fc257a4e1f7d953a748438e9383d47601edb2b`.
- Review branch: `feat/issue-157-runtime-executor-20261116`.
- Environment: existing VPS `debian-local`; no new VM/container; no model download, worker launch, or inference.
- CI evidence for starting commit: GitHub Actions run `44385353981` (`Python test suite`) completed with conclusion `success`.
- Targeted runtime/security tests: **152 passed** (executor, artifact handoff, authority, IPC, handlers, worker wrapper, revocation watchdog, broker restart integration, API/supervisor IPC integration, systemd unit hardening).
- Full repository test suite after the audit correction: **763 passed, 1 skipped, 43 subtests passed** in 26.21s, using `/home/debian/ai-chess-arena/.venv/bin/pytest` with `PYTHONPATH=.`.

### Findings and disposition

| ID | Area | Result | Severity | Evidence / action |
|---|---|---|---|---|
| IA-01 | Systemd test-recovery namespace validation | **FIXED** | Medium | Source inspection found `SystemdTransientBackend` accepted a `test-recovery` namespace for stop/recovery but its start validator still hard-coded the production unit regex and compared the production-generated unit name. Updated validation to use the instance's namespace allowlist and compare the complete policy properties independently of the namespace-specific unit name. Added a regression test proving test namespace start validation and preserving the pinned worker argv/policy. `tests/test_runtime_systemd_backend.py`: 20 passed; full suite passed. |
| IA-02 | Exact TEST_ONLY grant, batch approval, opt-in, preflight and artifact identity | **PASS (source + tests)** | — | Reviewed `runtime_executor.py` and `runtime_supervisor_authority.py`; authority resolves job IDs from the read-only canonical DB, requires both execution switches, a running non-cancelled claim, approved/unexpired batch at admission, exact model/repository/revision/SHA-256 TEST_ONLY grant, privileged artifact allowlist, matching quarantine record, fresh resource preflight, and bounded timeout. Revocation checks fail closed on DB/authority errors. Covered by the targeted tests above. |
| IA-03 | Artifact quarantine handoff and filesystem traversal controls | **PASS (source + tests)** | — | Reviewed `runtime_artifact_handoff.py`: canonical existing quarantine root, descriptor-relative traversal with `O_NOFOLLOW`, regular-file validation, root-contained source, pinned size/hash and per-job handoff permissions. Regression coverage included in the targeted suite. |
| IA-04 | IPC authentication and request boundary | **PASS (source + tests)** | — | Reviewed `runtime_supervisor_ipc.py`: Linux `SO_PEERCRED` required; exact allowed UID checked before request handling; bounded newline-framed JSON; duplicate keys and extra fields rejected; job-ID-only actions; errors do not expose exception/path/DB details. Socket startup validates parent and socket ownership/mode; stale cleanup probes for `ECONNREFUSED`, rechecks inode/identity, and refuses live listeners, symlinks, foreign sockets, regular files, and ambiguous errors. |
| IA-05 | Lease lifecycle, revocation and crash-recovery logic | **PASS (source + tests)** | — | Reviewed supervisor, broker and watchdog paths. Existing tests cover exact-unit lease persistence/recovery, cancellation/persist races, supervisor/broker restart, broker SIGKILL after commit-before-ack, and fail-closed stop behavior. This is code/test evidence, not a substitute for the live operational drill below. |
| IA-06 | systemd unit hardening and child/process containment | **PASS (static + tests + live worker SIGKILL)** | — | Reviewed fixed argv, rebuilt unit policy, effective systemd property verification, `KillMode=control-group`, bounded CPU/memory/tasks/runtime, and cgroup-v2 populated/direct-process checks. The actual supervised worker was started in the existing isolated container, passed real handoff/bundle/pinned-binary checks, received SIGKILL before any model subprocess was spawned, and its exact unit cgroup was verified empty after recovery. No model or inference ran. |
| IA-07 | Runtime logs and reviewed model-permission panel surfaces | **PASS (scoped source review)** | — | Runtime supervisor startup/watchdog logging emits fixed messages or exception class names, not exception text, paths, DB rows or command output. IPC responses likewise expose only exception class. Reviewed model-permission panel rendering uses `textContent` for model IDs, repository/revision/hash, requester and history; decision actions are explicit POST requests with confirmation/reason. Credential-pattern scan of runtime modules and reviewed panel surfaces found no embedded API keys, passwords, bearer tokens or secrets. Scope is limited to the runtime/security-related surfaces reviewed, not a certification of the entire legacy UI. |
| IA-08 | Operational disaster/restart drill for API, supervisor, broker, cancellation and unclean worker exit | **PASS — API/BROKER/SUPERVISOR + REAL WORKER SIGKILL** | — | Existing `arena-test` Debian 13 container drill verified API/broker/supervisor SIGKILL/restart, stale-socket recovery, HTTP readiness 200, exact synthetic unit recovery, durable lease/job reconciliation, SQLite integrity and FK checks. Follow-up used the real `runtime_worker` zipapp and exact test-only environment gate: worker passed handoff/bundle/pinned-binary checks, then SIGKILLed itself before bridge startup or `Popen`; systemd recorded `Result=signal`, `ExecMainStatus=9`, and no remaining cgroup. A fresh `RuntimeSupervisor` instance recovered the exact durable lease from SQLite (`active` → `stopped`). A 33-byte dummy placeholder was used, not a GGUF; llamafile was hash-verified but never executed. No model or inference ran. Evidence: `issue-157-container-crash-recovery-evidence-2026-11-18.json` and `issue-157-worker-crash-evidence-2026-11-18.json`. |
| IA-09 | SQLite lease/result transaction integrity and race ordering | **PASS (source + regression tests)** | — | Reviewed `SQLiteRuntimeUnitStore.persist/set_state/load_recoverable`, `RuntimeSupervisor.start/stop/recover`, `RuntimeJobAuthority` read-only query path, and executor terminal/cancel/revoke transitions. Lease writes use `BEGIN IMMEDIATE`, exact job/unit/state predicates, explicit allowed transitions, and commit-before-backend-start. Failed backend start remains recoverable as `start_uncertain`; recovery only stops exact durable namespace-validated unit identities. Result commit rechecks running job, cancellation flag, exact batch/grant/model/repo/revision/hash, TEST_ONLY scope and global DB switch under one immediate transaction. Cancellation/revocation serialize against result commit. Evidence: `tests/test_runtime_supervisor.py`, `tests/test_runtime_lease_broker.py` (including cancel-vs-persist race), `tests/test_runtime_executor.py` (including result-vs-cancel race), `tests/test_runtime_supervisor_authority.py`, and `tests/test_runtime_supervisor_broker_restart_integration.py`. Full suite on existing VPS: 763 passed, 1 skipped, 43 subtests passed (26.39s). Scope is code/test transaction behavior; not a substitute for the live abrupt restart matrix in IA-08. |
| IA-10 | AF_UNIX stale socket recovery after abrupt process termination | **PASS (source + regression tests + container drill)** | — | Initial SIGKILL drill showed both API/broker and supervisor left filesystem sockets that blocked restart. Added `_remove_stale_unix_socket()` in `runtime_supervisor_ipc.py`, reused by `runtime_lease_broker.py`: only an exact owner/group/mode socket with `ECONNREFUSED` and unchanged device/inode is removed; live listeners, non-sockets, foreign identity, inode changes, and ambiguous errors fail closed. Added a persistent mode-0600 startup lock acquired with `flock` and held for the listener lifetime, preventing concurrent starters from racing the probe/bind. Supervisor shutdown now unlinks only the inode it bound. Regression tests cover stale recovery, live/foreign path refusal, symlink lock refusal and concurrent-start serialization. Final rebuilt supervisor bundle SHA-256 `0264f8659824c0a4eacd18287336c5340f03faa97aa05f8e260165e1dbc8ebef`; final container crash/restart matrix passed. |

### Host safety snapshot after tests

- `ai-chess-arena.service`: **active**; production API was not restarted.
- `ai-chess-arena-runtime-supervisor.service`: **not-found / inactive / MainPID=0**; no production supervisor installation or start.
- Production database, Caddy, ACLs, legacy llamafile ownership/permissions and unrelated services were not changed by this audit pass.
- Tests ran in the existing repository worktree using disposable test fixtures; no model inference occurred.

### Current internal audit decision

**TECHNICAL AUDIT PASS — RELEASE-OWNER SIGN-OFF PENDING.** IA-01–IA-10 have documented dispositions; the stale-socket and transaction-boundary findings were remediated and regression-tested. IA-08 now includes the actual supervised worker process SIGKILL, exact durable-lease recovery, and empty-cgroup verification in the existing isolated container. The test-only hook is accepted only by the `test-recovery` unit namespace and is rejected by production validation. No model weights, model process, or inference were used. B5 technical audit and recovery-drill evidence are complete for the stated scope; the release owner must still accept the findings to close B5. B3 and B4 remain independently **OPEN**; this audit does not waive their requirements.


## Internal audit continuation — 2026-11-18 (operational drill evidence review)

- Reviewed branch HEAD: `cd92763c33a797bb600a8f759618eecd37d3e0a8`.
- GitHub Actions run `44386549028` (`Python test suite`, run 559): **success**.
- Re-ran recovery/restart-focused tests on the existing VPS: **89 passed in 4.52s** across `test_runtime_supervisor_broker_restart_integration.py`, `test_runtime_lease_broker.py`, `test_runtime_startup.py`, `test_runtime_supervisor_service.py`, `test_runtime_deployment.py`, and `test_runtime_executor.py`. Coverage includes broker outage and restart, supervisor restart against persisted exact leases, broker SIGKILL at commit/ack boundaries, cancellation/persist races, disabled execution and test-profile isolation.
- Reconciled prior live B1 evidence: the actual API and `Type=notify` supervisor were started on the existing VPS in the isolated recovery-only profile, reached HTTP/IPC readiness, recovered a seeded harmless transient systemd unit, persisted `stopped`, verified empty cgroup and SQLite integrity, rejected IPC start, and shut down gracefully with socket/unit cleanup. This is valid live startup/recovery evidence, but it is not an abrupt crash/restart matrix.
- Explicitly did not run an unclean worker exit: the test profile rejects worker start and no exact approved model/runtime artifact or inference authorization exists. This is a deliberate safety boundary, not a test failure; record as **NOT VERIFIED** until the relevant artifact and separate execution authorization exist.
- Production invariant snapshot after this pass: `ai-chess-arena.service` active, MainPID `3417317`; `ai-chess-arena-runtime-supervisor.service` `LoadState=not-found`, inactive, MainPID `0`; canonical `/usr/local/lib/ai-chess-arena-runtime` absent; no recovery staging roots or test transient unit files found. No production service/database/configuration was changed.

### IA-08 decision update

**PARTIAL — NOT VERIFIED.** Existing live B1 drill plus 89 passing restart/recovery tests provide meaningful evidence for startup/recovery, broker outage/restart, supervisor restart and cancellation races. Remaining high-severity evidence gap: a separately isolated live abrupt API/supervisor/broker crash-and-restart sequence has not been repeated as part of B5. Live unclean worker termination is blocked by the deliberate no-worker test profile and absent exact-artifact/inference approval; do not bypass that gate. B5 remains OPEN.


## Internal audit continuation — 2026-11-18 (transaction-boundary review)

- Reviewed branch HEAD before this record: `d3ff369de21108c4628536107c918eebfa779506`; latest associated GitHub Actions run `44386835632` (`Python test suite`, run 561): **success**.
- Re-reviewed durable runtime lease transitions and executor cancellation/result transaction boundaries. `SQLiteRuntimeUnitStore.persist()` and `set_state()` use `BEGIN IMMEDIATE`, exact row identity/state checks and a closed transition table; lease identity is committed before the systemd backend start. A backend start error is represented as `start_uncertain`, which remains recoverable. Recovery loads only the explicit recoverable states and validates each unit against the configured production or test namespace before stopping it.
- Re-reviewed privileged authority reads: the supervisor DB connection is opened with `mode=ro`, `query_only=ON`, and `trusted_schema=OFF`; revocation checks require the query-only invariant and propagate DB errors so the watchdog fails closed.
- Re-reviewed terminal result/cancellation ordering: result persistence uses `BEGIN IMMEDIATE` and rechecks the job's running/cancel state, batch identity/status, permission request identity/status/scope, exact model/repository/revision/hash tuple, and DB execution switch before committing. Cancellation and batch revocation use serialized immediate transactions.
- Regression evidence: `tests/test_runtime_supervisor.py`, `tests/test_runtime_lease_broker.py`, `tests/test_runtime_executor.py`, `tests/test_runtime_supervisor_authority.py`, and `tests/test_runtime_supervisor_broker_restart_integration.py`; the full repository suite on the existing VPS completed **763 passed, 1 skipped, 43 subtests passed** in 26.39s.
- IA-09 disposition: **PASS (source + regression tests)** for transaction ordering, exact lease transitions, and result/cancel/revoke race handling. This does not establish live crash behavior beyond IA-08's existing evidence.
- Production invariant: the production API was not restarted; no production DB/configuration or service was changed. No worker/model/inference was launched.

### IA-08 / B5 status after this continuation

IA-08 remains **PARTIAL — NOT VERIFIED (High)**. The recovery-only profile cannot launch a worker, and no exact model/runtime artifact or separate inference authorization exists; live unclean worker exit therefore remains deliberately untested. The remaining live abrupt API/supervisor/broker crash-and-restart matrix has not been performed. IA-09 adds source/test evidence but does not close IA-08. B5 remains **OPEN — MORE EVIDENCE REQUIRED**.


## Internal audit continuation — 2026-11-18 (isolated-container abrupt crash/restart matrix)

- Environment: reused the existing `/var/lib/machines/arena-test` Debian 13 image on VPS `vps-678a1be0` via `systemd-nspawn --private-network`; no new VM or container image was created. The current worktree and existing Python venv were bind-mounted read-only into the container. All test state was under the disposable container path `/var/tmp/arena-crash-recovery-20261118-01`.
- Runtime profile: `test-recovery`, explicit acknowledgement, systemd backend, execution flag `0`, loopback-only ephemeral HTTP port, disposable SQLite DB, distinct `test-recovery` systemd namespace, no worker artifact, and `allow_start=False`. The supervisor ran as actual `Type=notify`; API ran as unprivileged `debian`; synthetic units ran as `arena-runtime`.
- Built supervisor bundle from the current branch source inside the container. SHA-256: `95a68bd360b09bdb4d9d2b26449416c9f11d3993323ca0dcc7beb83b6bb1eef3`.
- Initial fault injection: abrupt `SIGKILL` of API killed its in-process lease broker and left `lease-broker.sock`; a new API process failed readiness because the broker refused the existing socket. Separately, abrupt `SIGKILL` of supervisor left `supervisor.sock`; a new `Type=notify` supervisor failed startup with `SupervisorIPCError`. Both stale entries were confirmed as sockets with exact expected owner/group/mode and `connect()` returned `ECONNREFUSED`.
- Remediation: added `_remove_stale_unix_socket()` in `runtime_supervisor_ipc.py` and reused it from `runtime_lease_broker.py`. Cleanup requires a socket (not symlink/regular file), exact expected UID/GID/mode, a refused connect, and an unchanged `(st_dev, st_ino)` immediately before unlink. Active listeners and ambiguous states fail closed. Supervisor finalization now unlinks only the exact socket inode it bound. Added regression coverage in `tests/test_runtime_supervisor_ipc.py` and `tests/test_runtime_lease_broker.py`.
- Full test suite on existing VPS after remediation: **766 passed, 1 skipped, 43 subtests passed** in 26.96s. Focused IPC/broker suite: **23 passed**.
- Repeat fault injection: seeded synthetic running job/active lease `910000103` and harmless `/usr/bin/sleep 600` transient unit `ai-chess-arena-runtime-test-910000103.service`. Sent `SIGKILL` to API (therefore broker) and supervisor. Both stale sockets remained, as expected. Restarted the actual supervisor from the rebuilt pinned zipapp and the API. Both reached `ActiveState=active`; supervisor sent `READY=1`; API reached HTTP readiness **200** on loopback.
- Recovery result: API startup requested supervisor recovery; the exact synthetic unit was stopped and unloaded (`LoadState=not-found`, `ActiveState=inactive`, empty `ControlGroup`); durable lease became `stopped`; executor reconciliation marked the synthetic job `failed`; SQLite `integrity_check=ok`, `foreign_key_check=[]`. IPC status reported the stopped lease. An IPC `start` request was rejected with `RuntimeSupervisorHandlerError` as required by the recovery-only profile.
- Process check through `/proc`: no `runtime_worker.py` or `llamafile` process was present. No worker, model, or inference was launched. Live unclean worker exit therefore remains **NOT VERIFIED** and is not to be simulated by bypassing the no-worker profile.
- Production safety snapshot: host `ai-chess-arena.service` remained active with MainPID `3417317`; host `ai-chess-arena-runtime-supervisor.service` remained `LoadState=not-found`, inactive, MainPID `0`. No production DB/configuration, Caddy, ACL, or unrelated service was changed.

### IA-08 decision after container drill

**PARTIAL — API/broker/supervisor crash matrix PASS; live unclean worker exit NOT VERIFIED (High).** The actual abrupt crash/restart sequence is now evidenced for API, its in-process broker, and the supervisor. The prior stale-socket restart blocker was fixed with fail-closed ownership/mode/connect/inode checks and regression tests. B5 remains **OPEN** because worker crash/exit behavior cannot be truthfully certified without an approved exact runtime/model artifact and separate inference authorization, and final internal release-owner sign-off remains outstanding. No external auditor is required.


### Runtime worker/supervisor regression continuation — 2026-11-18

- Reviewed the current worker entrypoint and launch contract. The production worker always verifies the per-job handoff/model snapshot, canonical root-owned bundle and pinned llamafile before starting the model subprocess. It has no inert live-worker crash mode; the recovery-only supervisor profile explicitly sets `allow_start=False`.
- Re-ran the focused worker/supervisor/runtime integration regression set on the existing VPS worktree with the repository root on `PYTHONPATH`: `tests/test_runtime_worker_supervised.py`, `tests/test_runtime_supervisor_handlers.py`, `tests/test_runtime_systemd_backend.py`, `tests/test_runtime_supervisor_broker_restart_integration.py`, `tests/test_runtime_revocation_watchdog.py`, and `tests/test_runtime_executor.py`.
- Result: **107 passed in 3.88s**. This includes worker argument and bundle validation, model snapshot ownership/mode/hash checks, pinned binary validation, fixed command construction, systemd unit/cgroup policy, lease/broker restart recovery, revocation and executor transaction behavior.
- The live worker SIGKILL scenario remains **NOT VERIFIED**. No synthetic replacement worker, altered production worker, model process, or inference was used. The real worker path still requires the exact admitted model artifact and a separate execution/inference authorization before a live worker process can be started. This regression run does not change IA-08 or B5 disposition.

### Final listener-lock serialization follow-up — 2026-11-18

After the first successful stale-socket recovery run, the startup path was hardened against two concurrent service starters racing between `ECONNREFUSED` and `bind()`. Both IPC servers now open a no-follow, exact-owner/group, mode-0600 lock file and hold a nonblocking exclusive `flock` for the full listener lifetime. The lock file is intentionally persistent so all starters contend on the same inode; the kernel releases the lock automatically after SIGKILL. A regression test proves a second starter cannot acquire the held lock and that a symlink lock file is refused.

The final code was rebuilt and retested in the reused `arena-test` Debian 13 image. Bundle SHA-256: `0264f8659824c0a4eacd18287336c5340f03faa97aa05f8e260165e1dbc8ebef`. With active synthetic lease/unit `910000105`, both API (and its broker thread) and supervisor were SIGKILLed. Both stale sockets returned `ECONNREFUSED`; restart acquired the existing lock files, safely removed stale socket inodes, and both services reached active/readiness. API returned HTTP 200; supervisor recovered the exact synthetic unit; lease state became `stopped`; job state became `failed`; DB integrity was `ok`, with no FK violations; IPC start remained blocked. Lock files were verified as `debian:debian 0600` and `root:root 0600`. No worker/model process was present. The container test root and test accounts/groups were removed and the existing image was powered off.

### B4 continuation — trust-model and governance inventory (2026-11-18)

- Selected a no-long-lived-secret candidate design for this public repository: GitHub Actions OIDC identity + Sigstore build-provenance attestations, verified against Sigstore trust roots and constrained to the exact repository/workflow/protected-main identity. Builder provenance is explicitly separate from release-owner approval and from deployment/inference authorization.
- Proposed a separate `runtime-release` GitHub Environment with the release owner as required reviewer; the approval payload must bind the exact source commit and worker/supervisor/llamafile/model digests. VPS pin-file installation remains a later, separately authorized step.
- Read-only GitHub API inventory: repository is public and Actions enabled, but `main` is not branch-protected, there are no repository rulesets, and there are no GitHub Environments. Existing CI workflow has only `contents: read`; it has no OIDC/attestation permissions and emits no attestation.
- Host verifier inventory: `gh` 2.46.0, `gpg` 2.4.7, OpenSSL 3.5.7; no `cosign` or `gpgv`. No key or trust root was generated/imported. No workflow, environment, branch protection, secret, repository setting, or production file was changed.
- B4 status: **OPEN — DESIGN SELECTED, GOVERNANCE AND ATTESTATION NOT IMPLEMENTED/VERIFIED**. The design is not evidence of a signature, approval, or deployable artifact. Required next gates are main protection, required CI, release Environment protection, pinned workflow implementation, attestation generation, constrained verification and negative tests.

### B4 implementation candidate — main-only builder attestations (2026-11-18)

- Added `.github/workflows/runtime-artifact-attestation.yml` on the PR branch. It runs only on pushes to `main`, deterministically builds worker and supervisor bundles, and creates a separate GitHub/Sigstore artifact attestation for each exact artifact path.
- Job permissions are limited to `contents: read`, `id-token: write`, and `attestations: write`; no deployment, model, inference, package-publish, or long-lived-secret access is granted. Actions are pinned to full commit SHAs and checkout credentials persistence is disabled.
- Action tag-to-commit references checked through GitHub API: `actions/attest` v4.2.2 `1e69f48acb82d1966a394da916b4c1698aa569d6`; `actions/checkout` v4.2.2 `11bd71901bbe5b1630ceea73d27597364c9af683`; `actions/setup-python` v5.6.0 `a26af69be951a213d495a4c3e4e4022e16d87065`.
- Workflow is not PR/feature-branch/workflow_dispatch-triggered, so this unmerged PR cannot mint an attestation that could be mistaken for a main release. No attestation run exists yet. Main is currently unprotected and no release Environment exists; protected-ref identity and exact-digest owner approval are not yet enforceable.
- B4 remains **OPEN** until repository governance, a successful main-ref attestation, VPS verification/negative tests, and separate authenticated exact-digest release-owner approval are completed.


### B4 continuation — protected release gate, digest-bound workflow and verifier (2026-11-18)

- `main` branch protection is active and API-verified: pull requests required, strict required status check `tests`, admin enforcement, force-push/deletion disabled, and conversation resolution required. Required approving review count is 0 because `MarVinPL` is currently the repository's only collaborator; this avoids a deadlocked self-review rule. Release approval is enforced separately by the Environment.
- GitHub Environment `runtime-release` is created and API-verified. Its only deployment branch policy is `main`; required reviewer is `MarVinPL`; self-review is allowed by explicit owner choice; administrator bypass is disabled; no environment secrets were added.
- `.github/workflows/runtime-artifact-attestation.yml` is main-push-only and has two jobs. `build-candidates` has only `contents: read`, builds both artifacts and publishes exact SHA-256 values in the Actions run summary before the approval gate. `attest` depends on that job and is protected by `runtime-release`; after approval it checks out the exact same source commit, rebuilds both artifacts, fails closed on any digest mismatch, then emits separate GitHub/Sigstore attestations for the worker, supervisor, and `release-approval.json`. The approval statement binds repository, `refs/heads/main`, source commit, workflow, environment, required reviewer, run ID, exact worker/supervisor hashes, and the limited `build-provenance-only` scope. It explicitly does not authorize runtime installation or model inference.
- Added `tools/verify_runtime_attestation.py`: checks regular non-symlink artifacts and full source commit ID, requires GitHub CLI >=2.102.0, then invokes `gh attestation verify` with exact repository, signer workflow, `refs/heads/main`, source commit digest, SLSA provenance predicate, GitHub Actions OIDC issuer, and hosted-runner-only policy. Its approval-statement validator checks the recorded reviewer policy, scope, source identity, run ID, and exact worker/supervisor digests. This is code/test evidence only until real main attestations and deployment-review records exist.
- GitHub CLI 2.105.0 was downloaded into a temporary `/var/tmp` directory solely to inspect the attestation verifier interface; the release archive matched the official release checksum. It was not installed or added to PATH. The host's existing GitHub CLI remains 2.46.0. Minimum verifier version 2.102.0 avoids the case-insensitive `--source-ref` comparison defect fixed in that release (GHSA-4mq3-hpgx-9cx8).
- Rebuilt worker and supervisor twice from the same current source tree on the VPS: both pairs were byte-identical. Worker SHA-256 `d7d7ab4be3637bb9c618e1e6d1ec67cbfcd7d847b791b31d045b7d7486d6dc1d`; supervisor SHA-256 `0264f8659824c0a4eacd18287336c5340f03faa97aa05f8e260165e1dbc8ebef`. Supervisor ZIP contains exactly 14 expected members and passes CRC validation. The differing earlier supervisor digest was from an earlier source commit, not nondeterminism.
- Targeted tests for the workflow policy, attestation verifier, release statement digest binding, and supervisor bundle: **13 passed**. YAML policy parse and `git diff --check`: PASS.
- The workflow has not run on `main`: PR #173 remains Draft and unmerged, and the workflow intentionally does not run on PR/feature refs. Therefore no real GitHub/Sigstore attestations exist yet. The actual Environment deployment review actor/decision must also be cross-checked against the GitHub deployment record when the first main run exists.
- B4 remains **OPEN** until the protected-main workflow runs, the user approves the exact candidate digest set, the three attestations are generated, the VPS verifies them with the pinned identity/source policy, and the GitHub deployment review record confirms the configured reviewer. No artifacts were installed; no model or inference was started.


## B5 continuation — real supervised worker SIGKILL and recovery (2026-11-18)

- Added `tools/issue157_worker_crash_drill.py`, an explicitly acknowledged, container-only drill. It refuses to run unless it is root inside `systemd-nspawn`, uses a new direct child of `/var/tmp` for its disposable SQLite DB, and operates only in the `test-recovery` unit namespace.
- Added a narrowly gated test hook to the real `runtime_worker_supervised.py`: after it verifies the immutable job handoff, root-owned worker bundle, and pinned llamafile hash, but before it creates the AF_UNIX bridge or calls `subprocess.Popen`, it sends `SIGKILL` to itself. The exact two-variable test environment is accepted only by `RuntimeSupervisor`/`SystemdTransientBackend` when configured with `unit_namespace="test-recovery"`; production namespace validation rejects it. Production systemd unit environments remain limited to API UID/GID.
- Executed on the existing VPS in the already-existing `/var/lib/machines/arena-test` Debian 13/systemd 257 image; no new image or VM was created. The worker was launched through `RuntimeSupervisor.start()` and the real `SystemdTransientBackend`, under exact test unit `ai-chess-arena-runtime-test-910000107.service`. The worker passed its real filesystem handoff, bundle path/ownership and binary SHA-256 checks.
- The worker unit ended with systemd `Result=signal`, `ExecMainCode=2`, `ExecMainStatus=9` (SIGKILL), `ActiveState=failed`, and empty `ControlGroup`. The durable lease was `active` after the worker crash. A new `RuntimeSupervisor` object using the same disposable SQLite lease store recovered exactly that unit and changed the lease to `stopped`; cgroup emptiness was confirmed.
- The model path contained only a 33-byte dummy placeholder, not a GGUF. The official-hash llamafile copy was present solely to exercise the worker's real binary verification; it was never executed. No model process, inference, or `Popen` child was created.
- Cleanup verified: transient unit stopped/reset, all test paths removed, temporary test accounts/groups removed, existing container powered off and retained. Host temporary source staging removed.
- Production snapshot: API `ai-chess-arena.service` remained active, MainPID `3417317`; production supervisor unit remained not-found/inactive; host `llamafile` and runtime worker process counts were zero. No production DB, service, config, ACL, canonical runtime bundle or model was changed.
- Machine-readable evidence: `issue-157-worker-crash-evidence-2026-11-18.json`. Worker bundle SHA-256: `b319d82830f1833ac891009ae7d3fe29abaac0b341c0d545adc7e98d8d2dcec9`; drill script SHA-256 is recorded in the JSON.
- Full VPS suite after the test-only worker hook and namespace validation: **780 passed, 1 skipped, 43 subtests passed**. `tools/issue157_worker_crash_drill.py` also passed `py_compile`.

**IA-08: PASS for the documented recovery/crash matrix. B5 technical audit: PASS; final release-owner sign-off remains pending.** This is an internal review; no independent/external auditor is required.

## B4 continuation — signed approval-statement verification and negative policy tests (2026-11-18)

- Extended `tools/verify_runtime_attestation.py` with `--approval-statement`. This path first runs the same GitHub/Sigstore attestation verification as ordinary artifacts (exact repo, signer workflow, protected-main ref, source commit, SLSA predicate, GitHub Actions OIDC issuer and hosted-runner restriction) and only then parses the approval payload.
- The approval payload validator now requires the in-toto Statement v1 schema/type, exact repository/ref/commit/workflow/environment/reviewer/scope, a numeric GitHub Actions run ID, an exact two-artifact set, and byte-derived worker/supervisor SHA-256 matches. Symlinks and non-regular files are rejected.
- Added negative tests for wrong repository, ref, commit, workflow, environment, reviewer, scope, extra artifacts, both digest mismatches, non-object JSON, and missing signed approval attestation. The tests also prove attestation verification happens before approval payload acceptance.
- Verifier-focused tests: **18 passed**. Full VPS suite: **793 passed, 1 skipped, 43 subtests passed**. `compileall` and `git diff --check`: PASS.
- No real main-ref approval statement or Sigstore attestation exists yet. B4 remains **OPEN** until the main-only workflow runs after merge, the exact digest set is approved in `runtime-release`, all three attestations are generated, the VPS verifies them, and the actual GitHub deployment review record is cross-checked.


## B3 continuation — metadata-only pin of Light-R1 candidate (2026-11-18)

Read-only Hugging Face API metadata confirmed the exact candidate identity: repository `360ZhiNao/Light-R1-14B-DS-GGUF`, revision `0d4531e58894e0019d4a9a6aa775eb32098213cb`, file `Light-R1-14B-Q4_0.gguf`, size `8,517,725,312` bytes, upstream LFS SHA-256 `d9fb85f0d6582ef0ac061dcf54e1236aff2d4583a83c0aebff281c5fb3764afa`. The repo tag and card metadata say `apache-2.0`; the revision has no `LICENSE`, `LICENSE.txt` or `COPYING` file. Therefore license status remains `pending_manual_review`, not `verified_permissive`. The upstream hash is recorded as metadata only and has not been recomputed from downloaded weights. No weights were downloaded, no artifact was placed in quarantine, no TEST_ONLY grant was activated, and no inference or tournament admission was authorized. See `issue-157-model-candidate-metadata-2026-11-18.json` and Issue #159.
