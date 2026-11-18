# Issue #157 — internal security audit packet (2026-11-18)

Status: **internal audit packet; audit execution and sign-off are tracked separately**. The implementation team may perform the audit. No external or independent auditor is a release prerequisite. This packet is not by itself a security certification, release approval, or authorization to install or execute runtime artifacts.

## Scope and current disposition

| Gate | Current status | Evidence / remaining requirement |
|---|---|---|
| B1 — API, broker, real systemd supervisor startup/recovery | **COMPLETE for the documented disposable integration scope** | See the B1 evidence section in `issue-157-runtime-api-cutover-plan-2026-11-18.md`. Does not authorize worker execution or production cutover. |
| B2 — SQLite WAL/sidecar backup and rollback | **COMPLETE for the documented disposable-copy scope** | See the B2 evidence section in the cutover plan. Production DB/journal mode were not changed. |
| B3 — runtime artifacts and model admission | **OPEN** | Reproducible candidate packaging is evidenced; runtime binary root-ownership/install contract is unmet; no exact GGUF repository, revision, filename, SHA-256, or licence has been approved; no model was downloaded and no inference permission exists. |
| B4 — authenticated provenance and signing | **OPEN** | Hash/source traceability exists, but there is no approved signing identity, trust-root distribution, authenticated release-owner approval, or signed attestation. The manifest is unsigned. |
| B5 — internal security audit and operational recovery drill | **OPEN** | Must be performed and evidenced by the implementation team using this checklist, with PASS/FAIL/NOT VERIFIED results, evidence references, finding severity, remediation, and final sign-off. No external auditor is required. |

## Evidence index

1. `issue-157-runtime-api-cutover-plan-2026-11-18.md` — scope, B1/B2 evidence, B3 artifact preflight, B4 trust-gap inventory, and explicit limitations.
2. `issue-157-artifact-manifest-2026-11-18.json` — candidate artifact hashes, sizes, modes, source commit/tree, reproducible-build result, ZIP member allowlists, and smoke-test outcomes.
3. PR #173 — implementation and review history: https://github.com/MarVinPL/ai-chess-arena/pull/173
4. Issue #157 — requested behavior and safety constraints: https://github.com/MarVinPL/ai-chess-arena/issues/157

## Candidate artifact identifiers (not approved for installation)

- Source commit used for the recorded candidate build: `45b1c8bb16a8792d2f578624dc0aa2d71b7d7220`
- `runtime_worker` SHA-256: `d7d7ab4be3637bb9c618e1e6d1ec67cbfcd7d847b791b31d045b7d7486d6dc1d` (9,574 bytes)
- `runtime_supervisor.pyz` SHA-256: `bddd166dc46d14f2dcb6d08ac7acfd41e7e46e8c023a51c343719a0f19bbeb02` (44,746 bytes)
- Two independent builds were byte-identical. This establishes reproducibility for the recorded build process, not trusted provenance or source approval.
- Existing legacy binary `/home/debian/ai-agent/bin/llamafile` has the expected hash but is owned by `debian:debian`; the worker requires UID 0. It was not modified, copied, or executed. Hash equality alone is not provenance.

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
| IA-04 | IPC authentication and request boundary | **PASS (source + tests)** | — | Reviewed `runtime_supervisor_ipc.py`: Linux `SO_PEERCRED` required; exact allowed UID checked before request handling; bounded newline-framed JSON; duplicate keys and extra fields rejected; job-ID-only actions; errors do not expose exception/path/DB details; socket path must not pre-exist and mode is restricted to 0600/0660. |
| IA-05 | Lease lifecycle, revocation and crash-recovery logic | **PASS (source + tests)** | — | Reviewed supervisor, broker and watchdog paths. Existing tests cover exact-unit lease persistence/recovery, cancellation/persist races, supervisor/broker restart, broker SIGKILL after commit-before-ack, and fail-closed stop behavior. This is code/test evidence, not a substitute for the live operational drill below. |
| IA-06 | systemd unit hardening and child/process containment | **PASS (static + tests); LIVE EXECUTION NOT VERIFIED** | — | Reviewed fixed argv, rebuilt unit policy, effective systemd property verification, `KillMode=control-group`, bounded CPU/memory/tasks/runtime, and cgroup-v2 populated/direct-process checks. No runtime worker or model was launched; live worker/cgroup termination remains unverified by design. |
| IA-07 | Logs, panel rendering, secret exposure and allowlist mutation | **NOT VERIFIED** | Low/Medium | Not yet completed in this audit pass. Requires separate targeted source review and evidence. |
| IA-08 | Operational disaster/restart drill for API, supervisor, broker, cancellation and unclean worker exit on disposable live profile | **NOT VERIFIED** | High | No production API restart was attempted. Existing B1/B2 evidence and automated broker/supervisor crash tests are recorded separately, but a complete B5 live disposable-profile drill has not yet been performed. Worker/model execution remains prohibited pending separate exact-artifact authorization and B3/B4 closure. |

### Host safety snapshot after tests

- `ai-chess-arena.service`: **active**; production API was not restarted.
- `ai-chess-arena-runtime-supervisor.service`: **not-found / inactive / MainPID=0**; no production supervisor installation or start.
- Production database, Caddy, ACLs, legacy llamafile ownership/permissions and unrelated services were not changed by this audit pass.
- Tests ran in the existing repository worktree using disposable test fixtures; no model inference occurred.

### Current internal audit decision

**MORE EVIDENCE REQUIRED.** The identified test-recovery validator defect has been fixed and regression-tested. B5 remains **OPEN** because IA-07 and IA-08 are not verified and the audit has not received final release-owner sign-off. B3 and B4 remain independently **OPEN**; this audit does not waive their requirements.
