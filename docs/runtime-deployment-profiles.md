# Runtime deployment profiles (Issue #157)

The deployment selector is intentionally separate from model runtime selection.
It controls the future test-executor integration only.

## Select a profile

- `ARENA_RUNTIME_DEPLOYMENT=disabled` (default): runtime executor integration is off.
- `ARENA_RUNTIME_DEPLOYMENT=staging`: inert profile; no model process, systemd unit,
  database lease, or supervisor socket is touched. Setting the legacy global
  execution flag cannot make staging executable.
- `ARENA_RUNTIME_DEPLOYMENT=production`: explicit production contract. It requires
  `ARENA_RUNTIME_PRODUCTION_ACK=I_UNDERSTAND_RUNTIME_EXECUTION`,
  `ARENA_RUNTIME_BACKEND=systemd`, `ARENA_RUNTIME_EXECUTION_ENABLED=1`, and an
  absolute supervisor socket path under `/run/`. Artifact, output and worker
  roots are pinned to dedicated canonical paths.

The profile resolver is pure configuration: it does not create directories,
connect to a socket, open the arena database, invoke systemd, or start a model.
`server.py` uses it to decide whether to start the production-only dispatcher.
Production profile resolution is **not** proof that the privileged supervisor,
root-owned worker bundle, artifact handoff, namespace bridge, effective
systemd property verification, and crash-recovery path have been provisioned
on this host. Those remain release gates; missing supervisor dependencies block API
startup rather than falling back to an unsandboxed runner.

## Switching later

Production enablement requires separately provisioned supervisor/worker assets and directories, a reviewed exact artifact allowlist, the production profile variables in the API and supervisor environments, and a separately approved deployment/restart. Do not copy staging values into production or enable the legacy executor flag alone.

## Current safety status

The profile module is wired into `server.py`. The API dispatcher is started only in the production profile after successful supervisor recovery; the profile defaults to disabled and staging is always inert. The production profile and independent opt-in are not enabled on the VPS, and this PR does not install the privileged supervisor service or worker bundle. No production configuration or host state was changed.
