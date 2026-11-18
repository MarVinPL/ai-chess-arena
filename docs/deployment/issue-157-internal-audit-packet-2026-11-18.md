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

- [ ] Agree on an organizationally controlled signing identity and verifier/trust-root distribution; do not trust a key created solely by the build operator.
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
