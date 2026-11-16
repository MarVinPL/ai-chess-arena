# Trusted supervisor job authority

runtime_supervisor_authority.py is the privileged-handler boundary for the
narrow AF_UNIX protocol. The IPC request contains only job_id; no
caller-supplied command, executable, path, environment, unit name, or systemd
property is accepted.

## Start authorization

RuntimeJobAuthority.resolve_start(job_id) opens a read-only SQLite connection
and independently checks, in one read snapshot:

- global ARENA_RUNTIME_EXECUTION_ENABLED=1 opt-in;
- persisted runtime_execution_settings.enabled=1;
- an explicit, injected, non-empty privileged artifact allowlist;
- the job exists, is already claimed as running by the API executor, and has
  cancel_requested=0;
- the job belongs to a batch whose status is approved or running and whose
  approval expiry is still in the future;
- the exact referenced permission request remains approved with scope
  TEST_ONLY and matches model, repository, revision, and artifact SHA-256;
- the independently computed active TEST_ONLY scope still exists;
- the verified-quarantine registry row exists and matches artifact key,
  source path, size and SHA-256;
- the persisted runtime preflight is ready_pending_approval, matches the
  registry identity, and is no older than 15 minutes;
- the current host RAM and quarantine-volume free-space reserves still pass;
- candidate artifacts marked review-required have a positive, exact-revision
  manual license/technical review. Declared permissive licenses require
  Apache-2.0 evidence; all review-required candidates need a confirmed
  revision, ready technical status and no blockers;
- artifact-key syntax and bounded runtime timeout are valid.

The allowlist and review-required set must be injected from trusted,
version-controlled policy by the privileged service. The IPC caller cannot
supply or modify either set.

The database connection is opened with SQLite mode=ro, query_only=ON, and
trusted_schema=OFF. The authority refuses injected connections unless
PRAGMA query_only is active. It performs no schema migration or writes.

## Artifact handoff

The resolver returns the exact verified registry source path, size and digest
inside a frozen authorization record. runtime_artifact_handoff.py copies from
that path using directory file descriptors and O_NOFOLLOW, hashes the bytes
during the copy, and creates a new per-job snapshot with root/runtime-group
ownership and read-only runtime permissions. The helper does not create the
handoff root or provision any host identity.

The supervisor must resolve the job again immediately before starting the
systemd unit, after the copy, to close the permission-revocation window. It
must retain the durable unit lease until the exact unit is stopped and its
cgroup is empty; only then may it remove the per-job handoff.

## Deliberate non-goals

The authority and handoff helper do not create a systemd unit, persist a
privileged lease, connect the IPC server to the API, mount paths into a
namespace, or launch a worker. The current worker/bundle interface mismatch
and systemd property-normalization risks remain unresolved.

Tests use isolated temporary SQLite databases and temporary directories.
They do not access the production database or quarantine, provision accounts,
change ACLs, create persistent host resources, or run a model.
