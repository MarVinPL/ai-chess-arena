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
Production profile resolution is **not** proof that the privileged supervisor,
root-owned worker bundle, artifact handoff, namespace bridge, effective systemd
property verification, and crash-recovery path have been installed and tested.
Those are still release gates. Until the supervisor transport and worker bundle
are implemented and reviewed, production must remain disabled and this module
must not be used as an execution authorization source.

## Switching later

Once all release gates are complete, the deployment change is configuration-only:
set the production profile and its required variables in the dedicated service
configuration, then perform the separately approved deployment/restart. Do not
copy staging values into production or enable the legacy executor flag alone.

## Current safety status

This profile module is not wired into `server.py` or the job executor yet. That is
intentional: wiring it before the privileged supervisor transport and worker
bundle are ready would create a misleading production switch. No production
configuration or host state is changed by this change.
