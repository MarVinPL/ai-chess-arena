# Per-job immutable artifact handoff prototype

runtime_artifact_handoff.py copies one exact registry-pinned artifact into a
root-managed per-job snapshot. The quarantine path is untrusted input even
after the authority resolver has checked the model/repo/revision/SHA.

## Host-side ownership and isolation

- Handoff root: supervisor:supervisor, mode 0711.
- Per-job directory: supervisor:supervisor, mode 0700.
- Exact artifact: arena-runtime:supervisor, mode 0400.
- The runtime UID cannot traverse the host handoff root or job directory.
- The artifact is exposed only through the unit's exact
  BindReadOnlyPaths=source:/run/arena-runtime/job-ID/model.gguf mount.
- The unit also has InaccessiblePaths=/var/lib/arena-handoff, so the runtime
  cannot enumerate or open other host-side job artifacts.
- ReadWritePaths is limited to the per-job /run/arena-runtime/job-ID
  directory; the artifact mount overlays its root-owned, mode-000 placeholder
  and remains read-only.
- The handoff volume must have at least artifact size + 512 MiB free before
  copy. The helper also streams and verifies exact size and SHA-256.

The root and per-job directories are not group-readable by the runtime. This
avoids the shared-runtime-group flaw where one job's runtime UID could traverse
another job's artifact directory. The runtime receives only the bind-mounted
file at the output path.

## Cleanup

After the exact systemd unit cgroup is confirmed empty,
remove_artifact_handoff() removes only the exact per-job file/directory. It
checks owner/group/mode and refuses recursive cleanup of unexpected files.
The helper never creates the host handoff root, users/groups, ACLs, sockets,
units, cgroups or network configuration.

Tests use temporary directories and the current test identity. No production
quarantine file, handoff root, runtime identity or host permission was changed.
