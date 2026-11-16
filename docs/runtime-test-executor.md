# Runtime test executor — Issue #157

## Current implementation boundary

`runtime_executor.py` provides a durable, sequential job queue. Enqueue requires an already-approved `TEST_ONLY` permission for the exact `model_id`, repository, full revision SHA, and GGUF SHA-256. It stores the requesting actor and exposes job state to the authenticated panel. Queued jobs can be cancelled.

The execution entry point is deliberately not wired to a background worker or HTTP action. `execute_job()` refuses to run unless a trusted runner is explicitly injected by a future integration. Before calling that runner it:

1. acquires a process-wide `flock` (one runtime test globally);
2. checks the exact approved `TEST_ONLY` grant again;
3. marks only one queued job as running;
4. calls the quarantine handoff gate, which refreshes resource/provenance checks and re-hashes the exact file;
5. checks cancellation and permission revocation before and after the runner call;
6. persists completion, cancellation, or failure.

The runner contract receives the approved artifact path, timeout budget, and a cancellation callback. A production runner must implement a bounded subprocess lifecycle (process group, hard timeout, TERM/KILL escalation, stdout/stderr capture, and cleanup) and poll cancellation. No generic shell command or user-provided executable is accepted by this module.

## Explicitly not enabled

- No background executor worker or automatic start.
- No default runner or model launcher.
- No inference, model download, artifact move, allowlist change, tournament admission, or tournament start.
- No production deployment or systemd change.

This is an integration foundation, not completion of Issue #157. Runtime execution remains unavailable until the trusted model-specific adapter, hard process timeout/cancellation, isolated output directory, and end-to-end tests are implemented and reviewed.
