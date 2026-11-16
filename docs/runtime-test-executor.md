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


## Two-stage execution authorization (default off)

The queue is not executable merely because a model has a matching TEST_ONLY
grant. Runtime execution now has two additional gates:

1. A deployment-level opt-in: ARENA_RUNTIME_EXECUTION_ENABLED=1.
2. A persisted operator switch, initially disabled, changed only through the
   authenticated /api/hf-candidates/runtime-enable endpoint with explicit
   confirmation and a reason.

Queued jobs must be grouped into a batch through
POST /api/hf-candidates/runtime-batch-create. The exact batch then requires a
separate operator approval through
POST /api/hf-candidates/runtime-batch-approve; approval expires after at most
10 minutes if the batch has not begun. An approved batch can be revoked through
POST /api/hf-candidates/runtime-batch-revoke. Disabling runtime revokes all
pending/approved/running batches, cancels queued jobs, and signals active jobs
to stop. Approval and enablement do not start a model.

There is still no background worker, run endpoint, default runner, model
process, or inference path. The execution function continues to require an
explicitly injected runner. The deployment opt-in is not enabled by this PR.


## Trusted subprocess adapter (not wired or invoked)

runtime_subprocess.LlamafileSmokeRunner is the fixed-command adapter candidate.
It accepts only a regular file under the quarantine root, recomputes the
artifact SHA-256, and launches the repository-owned runtime_worker.py using
the current Python interpreter. The worker accepts no executable or shell
command from the request; it verifies the fixed llamafile path and pinned
binary SHA-256 before execve.

The parent starts a dedicated session/process group, applies a hard wall-clock
deadline, polls cancellation while loading and during the smoke inference, and
sends SIGTERM followed by SIGKILL to that owned group. The worker applies
CPU-time, file-size, file-descriptor and core-dump limits. Logs and temporary
files are isolated in a private temporary directory and removed after exit.
The adapter performs one fixed smoke inference after the local health check.

This adapter is deliberately not a default runner, worker, HTTP execution
endpoint, or scheduled task. It is not invoked by tests. Tests use fake process
objects and a harmless short-lived Python child solely to validate supervision.
No GGUF model, llamafile process, or inference has been started by this change.


## Crash recovery

The executor persists the owned process PID and Linux process start-time ticks
when the trusted adapter starts. On server startup, recover_orphaned_jobs()
marks stale running jobs failed. It sends TERM/KILL only when procfs confirms
the same start-time identity, the process is both process-group and session
leader, and its command line/executable matches the fixed runtime worker or
pinned llamafile path. A reused PID, missing identity, or unrelated process is
never signalled; the job is still marked failed with an explanatory record.


## Remaining security boundary

The subprocess adapter provides lifecycle ownership, a dedicated process group,
resource limits, and temporary-output isolation. It is **not** an OS security
sandbox: it does not yet use a separate UID, namespaces, seccomp, cgroups, or a
network namespace. It therefore must not be treated as a complete containment
boundary for a compromised runtime. These controls, a memory-enforcement
strategy, and final end-to-end/recovery tests remain prerequisites before
wiring the adapter into an executor worker or considering Issue #157 complete.
