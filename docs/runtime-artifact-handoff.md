# Per-job immutable artifact handoff prototype

runtime_artifact_handoff.py implements a code-only artifact snapshot primitive
for the future privileged supervisor. The quarantine registry path is treated
as untrusted input even after the database authority has checked the artifact
key, size and SHA-256.

## Filesystem contract

- Source must be opened beneath the configured quarantine root using directory
  file descriptors and O_NOFOLLOW for every path component and the final file.
- The source must be a regular file with the pinned size.
- Bytes are copied from the opened file descriptor into a newly created,
  exclusive per-job destination; the digest is computed while copying.
- The destination is accepted only when both byte count and SHA-256 match the
  exact job authorization.
- The destination is owned by the supervisor UID and runtime GID:
  handoff root 0710, per-job directory 0750, artifact 0440.
- The handoff root must already exist with the expected owner/GID/mode, and
  its volume must have at least the artifact size plus a 512 MiB free-space
  reserve. The helper does not provision or modify the root, accounts, groups,
  ACLs, units, sockets, cgroups, or network configuration.
- Existing per-job directories are never overwritten. Failed partial copies
  are cleaned up. After a verified cgroup stop, remove_artifact_handoff()
  removes only the exact job directory and refuses recursive cleanup of any
  unexpected files.

The runtime account can traverse the root and read its exact job artifact via
the runtime group. The API identity must not belong to that group. The root
and job directory are not writable by the runtime account.

## Deliberate non-goals

The helper does not read the Arena database, resolve the registry row, decide
whether a model is authorized, delete old handoffs, mount paths into systemd,
or launch a worker. The caller must supply the exact registry path, pinned
size/SHA-256 and runtime GID from trusted configuration/authority. The
supervisor must revalidate job/batch/permission state immediately before start
and own cleanup after the exact systemd unit is confirmed stopped.

Tests use temporary directories and the current test UID/GID only. No actual
quarantine file, production handoff root, runtime identity, or host permission
was changed.
