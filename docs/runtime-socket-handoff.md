# Per-job API/runtime socket and output handoff

runtime_socket_handoff.py creates and verifies the job-specific output
directory. The output root must already exist as supervisor:supervisor mode
0755; the helper creates only the exact job-ID child.

## Directory contents and DAC

- Job directory: owner arena-runtime, group API primary group, mode 02710.
  Runtime can create the bridge socket; API group can traverse/connect but
  cannot list, create, unlink or replace entries.
- control.sock: runtime UID, API GID (inherited through setgid), mode 0660.
- model.gguf: root-owned, root-group, zero-byte mode-000 placeholder created
  before unit start. The systemd unit overlays this exact target with the
  verified runtime-owned source file through BindReadOnlyPaths. After the
  namespace is gone, the root supervisor removes the placeholder.
- Cleanup accepts only the expected control.sock and model.gguf placeholder;
  every other file causes a refusal.

The worker bridge is configured with cleanup_socket=False. It deliberately
leaves control.sock in place when it exits. Only the privileged supervisor
removes the socket and placeholder, and only after the exact unit cgroup is
confirmed empty. Cleanup is non-recursive and refuses unexpected files,
symlinks, ownership changes or mode changes.

The helper does not create /run/arena-runtime, runtime accounts/groups,
ACLs, systemd units or mount namespaces. Tests use temporary directories and
the current UID/GID only; no host state changed.
