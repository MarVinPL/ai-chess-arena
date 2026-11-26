# Decision: Remote Worker Gateway as a separate VPS systemd service

Status: **accepted by project owner, 2026-11-22**. Implementation pending.
Tracking: Issue #192, PR #193. This decision is not a deployment approval.

## Architecture

- Keep the main **AI Chess Arena** server responsible for tournaments,
  scheduling, the live chessboard and the web panel.
- Run the **Remote Worker Gateway** in a **separate systemd service** on the
  existing Debian VPS, proposed unit name `ai-chess-remote-worker.service`.
- The gateway handles authenticated remote worker communication, job delivery,
  result ingestion and worker state. Caddy terminates HTTPS and proxies to a
  loopback-only gateway listener. No public HTTP listener.
- Remote executors may eventually run on the VPS, Android Termux or other
  computers. Workers perform inference and return proposed chess moves; they
  never directly modify tournament checkpoints.
- Use the existing SQLite durable job/result journal. Enforce one
  coordinator-owned checkpoint writer and atomic compare-and-swap before
  applying remote results. A gateway must not independently overwrite the
  main server's tournament state.

## Operations and security

- Independent process lifecycle, status, logs and restart policy via systemd.
- Resource restrictions and service hardening, with limits chosen after tests
  against available VPS resources; do not starve the main arena service.
- Credential authentication, TLS certificate verification for remote clients,
  access controls, request limits, safe restart reconciliation and an
  auditable deployment/rollback procedure are required before exposure.
- Preserve local runner behavior as default. Remote dispatch is **disabled
  by default** until integration and safety review are complete.
- First run isolated tests on the **existing VPS**, without changing live
  Caddy configuration, the main service, production data or Android.
- Only free/open-source components (Python, systemd, Caddy, SQLite), with
  **no additional paid service or budget**.

## Delivery discipline

All changes follow branch + PR to `main`, one Issue per PR; no direct push to
`main`. Record test evidence and obtain approval before production enablement.
This document records the agreed target architecture; it does not claim that
`ai-chess-remote-worker.service` is already installed or running.
