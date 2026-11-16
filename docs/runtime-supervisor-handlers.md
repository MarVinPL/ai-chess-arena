# Privileged supervisor handlers (Issue #157)

runtime_supervisor_handlers.py composes the narrow job-ID-only IPC protocol
with the trusted authority resolver, per-job artifact handoff, durable unit
lease store and injected runtime supervisor.

## Start flow

For a single positive job ID, the handler:

1. Refuses a duplicate durable lease.
2. Resolves the job from the read-only authority. This checks runtime opt-in,
   DB enablement, the API's running claim, cancellation, batch expiry, exact
   TEST_ONLY grant, explicit privileged artifact allowlist, verified registry,
   fresh preflight, current RAM/disk reserves and required manual review.
3. Copies the exact source into the pre-provisioned handoff root and verifies
   copied byte count and SHA-256.
4. Resolves the job a second time after the copy and requires the same pinned
   job/grant/batch/artifact identity.
5. Builds the fixed worker argv and per-job systemd spec from trusted code and
   the authorization record.
6. Persists the exact deterministic unit lease before calling the backend.

If failure occurs before a lease exists, the handler removes only that exact
job handoff. If a lease exists, start outcome may be ambiguous; the artifact is
retained for exact-unit recovery.

## Stop, status and recovery

- stop() loads the persisted lease and stops only that exact validated unit.
  Artifact cleanup happens only after the backend confirms the unit cgroup is
  empty. Cleanup is non-recursive and checks exact owner/group/modes.
- status() reports the durable lease state only. It does not claim that the
  systemd unit is live or expose an inferred ActiveState.
- recover() first asks RuntimeSupervisor to stop all persisted recoverable
  units, then removes handoff directories whose lease is absent or stopped.
  A unit start is only requested after lease persistence, so an unleased
  handoff cannot represent a process started through this handler.
- An ambiguous start or failed stop keeps the lease and handoff for retry.

The IPC response uses unit_state=start_requested, not runtime_started=true.
systemd-run is invoked with --no-block in the backend prototype; the handler
does not claim that inference has begun. The durable lease state "active" is
a lease lifecycle state, not proof of a live model process.

## Fixed policy

The default spec builder uses:
- runtime identity arena-runtime:arena-runtime;
- the exact per-job artifact under /var/lib/arena-handoff;
- output directory /run/arena-runtime/job-<id>;
- MemoryMax equal to the same conservative estimate used by preflight;
- MemorySwapMax=0, CPUQuota=100%, TasksMax=8;
- RuntimeMaxSec equal to the approved job timeout;
- the existing strict filesystem, namespace, network and cgroup policy.

A spec-builder injection exists for deterministic unit tests only. Production
must use the default builder and the canonical handoff root. The handler does
not provision the root, runtime account/group, output directories, socket,
systemd unit, cgroup, mount, or network configuration.

## Validation boundary

Tests use temporary SQLite databases, temporary quarantine/handoff
directories, and a fake backend. They exercise the IPC start/stop/status
round-trip, lease-before-start ordering, ambiguous-start retention, exact-unit
recovery, authorization changes during copy, and safe handoff cleanup.

This is not yet wired into server.py, the executor, or a persistent supervisor
service. No real systemd command, model worker, download, inference, tournament
or production configuration was exercised. The current worker bundle/argv
contract and actual systemd property normalization remain open blockers.
