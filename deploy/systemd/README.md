# Dedicated systemd service

ai-chess-arena.service is the version-controlled service definition for the
Arena API. It is intentionally separate from sentinelx-cloud-core.service.

## Runtime contract

- Runs as debian:debian from /home/debian/ai-chess-arena.
- Starts the repository virtualenv Python and server.py.
- The current application binds the API to 127.0.0.1:8090.
- ProtectHome=read-only keeps /home read-only by default. BindPaths exposes
  only /home/debian/ai-chess-arena as a writable bind mount, allowing SQLite,
  logs, and Arena-owned model files to be updated.
- InaccessiblePaths hides /home/debian/ai-agent/models entirely.
- The llamafile executable is exposed read-only at
  /home/debian/ai-agent/bin/llamafile.
- KillMode=control-group ensures child processes remain in the service
  lifecycle. Do not use SentinelX's service as a restart mechanism.

## Installation gate

This file is committed to Git only. It has not been copied to
/etc/systemd/system, enabled, started, or applied.

Before installing or starting it:
1. Obtain separate, explicit approval for the deployment and restart.
2. Follow the VPS deployment/rollback runbook in
   docs/deployment/vps-controlled-deployment-runbook-2026-11-13.md.
3. Back up and validate the production database and application files.
4. Arrange a controlled handover of the existing API process. The current
   process is not owned by an ai-chess-arena.service unit and shares the
   sentinelx-cloud-core.service cgroup. Do not restart or stop SentinelX.
5. Confirm port 8090 is free before starting this service. Do not run two API
   processes against the same database.

Only after those gates are met should an operator install the unit, reload
systemd, and start/enable ai-chess-arena.service.

## Validation

Offline validation:
- systemd-analyze verify deploy/systemd/ai-chess-arena.service
- python -m pytest -q tests/test_systemd_unit.py

No systemd state is changed by either validation command.

## Rollback

Rollback requires separate approval. Stop only ai-chess-arena.service, restore
the application/database snapshots according to the deployment runbook, and
verify the API and SQLite integrity before any tournament is started. Never
stop or restart sentinelx-cloud-core.service as part of Arena rollback.
