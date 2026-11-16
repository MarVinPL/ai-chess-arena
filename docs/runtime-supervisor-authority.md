# Trusted supervisor job authority

`runtime_supervisor_authority.py` is the first privileged-handler boundary for the narrow
AF_UNIX protocol. The IPC request contains only `job_id`; no caller-supplied
command, executable, path, environment, unit name, or systemd property is
accepted.

## Start authorization

`RuntimeJobAuthority.resolve_start(job_id)` opens a read-only SQLite connection
and independently checks, in one read snapshot:

- global `ARENA_RUNTIME_EXECUTION_ENABLED=1` opt-in;
- persisted `runtime_execution_settings.enabled=1`;
- the job exists, is already claimed as `running` by the API executor, and has
  `cancel_requested=0`;
- the job belongs to a batch whose status is `approved` or `running` and whose
  approval expiry is still in the future;
- the exact referenced permission request remains `approved` with scope
  `TEST_ONLY` and matches model, repository, revision, and artifact SHA-256;
- the independently computed active `TEST_ONLY` scope still exists;
- artifact key syntax/revision and bounded runtime timeout are valid.

The database connection is opened with SQLite `mode=ro`, `query_only=ON`, and
`trusted_schema=OFF`. The authority refuses injected connections unless
`PRAGMA query_only` is active. It performs no schema migration or writes.

## Deliberate non-goals

This module returns only a frozen authorization record. It does not resolve a
filesystem path, read/copy a quarantined artifact, create a handoff directory,
build a systemd unit, persist a unit lease, or launch/stop a process. Those
operations require separate reviewed components and integration tests.

The API executor currently transitions a job to `running` before calling its
injected runner; this is the execution claim the privileged side expects. The
supervisor still rechecks every independent permission and batch gate, so a
status transition alone is insufficient to authorize a start.

## Validation evidence

`tests/test_runtime_supervisor_authority.py` uses an isolated temporary SQLite
database and verifies successful resolution, query-only enforcement, opt-in,
job claim/cancellation, batch expiry/revocation, exact grant identity, and
invalid job IDs. No production DB, host identity, artifact, systemd unit, or
model process is accessed.
