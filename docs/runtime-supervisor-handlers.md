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
3. Copies the exact source into the pre-provisioned supervisor-only handoff
   root and verifies copied byte count and SHA-256. The runtime cannot traverse
   this host-side root.
4. Creates the pre-provisioned per-job /run output directory and its
   root-owned, mode-000 model mount placeholder. The systemd spec bind-mounts
   only the exact artifact into this directory and hides the host handoff root.
5. Resolves the job a second time after the copy and requires the same pinned
   job/grant/batch/artifact identity.
6. Builds the fixed worker argv and per-job systemd spec from trusted code and
   the authorization record.
7. Persists the exact deterministic unit lease before calling the backend.
8. The supervisor checks cancel_requested atomically with lease persistence,
   then runs a final authority callback after persistence and immediately
   before the backend call. If this final check fails, the lease is marked
   stopped without invoking the backend. The API-side client polls cancellation
   during health and inference and requests exact-unit stop after a cancellation.

If failure occurs before a lease exists, or the final pre-start check safely
marks the lease stopped, the handler removes only that exact job handoff. If
a lease remains in a recoverable state, start outcome may be ambiguous; the
artifact is retained for exact-unit recovery.

## Stop, status and recovery

- stop() loads the persisted lease and stops only that exact validated unit.
  Artifact and output-directory cleanup happen only after the backend confirms
  the unit cgroup is empty. Cleanup is non-recursive and checks exact owner/group/modes.
- status() reports the durable lease state only. It does not claim that the
  systemd unit is live or expose an inferred ActiveState.
- recover() first asks RuntimeSupervisor to stop all persisted recoverable
  units, then removes handoff directories whose lease is absent or stopped.
  A unit start is only requested after lease persistence, so an unleased
  handoff cannot represent a process started through this handler.
- An ambiguous start or failed stop keeps the lease and handoff for retry.

The IPC response uses unit_state=start_requested, not runtime_started=true.
systemd-run is invoked with --no-block by the transient systemd backend; the handler
does not claim that inference has begun. The durable lease state "active" is
a lease lifecycle state, not proof of a live model process.

## Fixed policy

The default spec builder uses:
- runtime identity arena-runtime:arena-runtime;
- the exact host-side per-job artifact, bind-mounted read-only as
  /run/arena-runtime/job-<id>/model.gguf while /var/lib/arena-handoff is hidden;
- output directory /run/arena-runtime/job-<id>, with a pre-created root-owned
  mode-000 model mount placeholder and the API/runtime socket handoff;
- MemoryMax equal to the model-memory estimate; the separate 1.5 GiB host
  reserve is excluded from the runtime cgroup and must remain available outside it;
- MemorySwapMax=0, CPUQuota=100%, TasksMax=8;
- RuntimeMaxSec equal to the approved job timeout;
- the existing strict filesystem, namespace, network and cgroup policy.

A spec-builder injection exists for deterministic unit tests only. Production
must use the default builder and the canonical handoff root. The handler does not provision the handoff/output roots, runtime account or
group, systemd unit, cgroup, mount namespace or network configuration. It
creates only the per-job artifact and output children beneath pre-provisioned
roots, and removes them only after verified cgroup teardown.

## Validation boundary

Tests use temporary SQLite databases, temporary quarantine/handoff
directories, and a fake backend. They exercise the IPC start/stop/status
round-trip, lease-before-start ordering, final authorization after lease
persistence, cancelled-job rejection, ambiguous-start retention, exact-unit
recovery, authorization changes during copy, and safe handoff cleanup.

This is not yet wired into server.py, the executor, or a persistent supervisor
service. A narrow race remains between the final DB authorization check and
systemd accepting the start request; the API client stops immediately after
the start response if cancellation is pending, while a continuous
supervisor-side revocation watcher remains an integration requirement. No real
systemd command, model worker, download, inference, tournament or production
configuration was exercised. The source worker entrypoint now matches the
fixed --job ID argv contract, but it has not been installed into the root-owned
bundle. Actual systemd property normalization and mount-namespace behavior
remain open blockers.
