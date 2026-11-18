# Runtime test executor — Issue #157

## Current implementation boundary

`runtime_executor.py` provides a durable, sequential job queue. Enqueue requires an already-approved `TEST_ONLY` permission for the exact `model_id`, repository, full revision SHA, and GGUF SHA-256. It stores the requesting actor and exposes job state to the authenticated panel. Queued jobs can be cancelled.

The production API path now wires `RuntimeApiWorker` to `RuntimeSupervisorClientRunner` only after successful supervisor recovery. Disabled and staging profiles do not construct or start this worker. `execute_job()` still refuses to run without an explicitly injected trusted runner. The authenticated HTTP actions manage queue, batch and execution gates; there is no public one-click run endpoint. Before calling the runner it:

1. acquires a process-wide `flock` (one runtime test globally);
2. checks the exact approved `TEST_ONLY` grant again;
3. marks only one queued job as running;
4. calls the quarantine handoff gate, which refreshes resource/provenance checks and re-hashes the exact file;
5. checks cancellation and permission revocation before and after the runner call;
6. persists completion, cancellation, or failure.

The runner contract receives the approved artifact path, timeout budget, and a cancellation callback. The wired production runner sends only the job ID to the privileged supervisor; the supervisor independently resolves authorization, creates the artifact/socket handoff and starts the exact transient systemd unit. Model health and the fixed smoke inference are reached over the per-job AF_UNIX bridge. No generic shell command or user-provided executable is accepted.

## Explicitly not enabled

- The API dispatcher is wired but remains inert unless the production deployment profile, independent environment opt-in, persisted operator switch, approved batch and exact TEST_ONLY grant are all active.
- The production profile is not enabled on the VPS; no real model or inference has been run.
- The root supervisor service and immutable worker bundle are not installed/enabled by this PR. Production startup therefore fails closed unless those separately provisioned dependencies are present.

This remains an integration foundation, not completion of Issue #157. Disposable-environment validation and the documented internal security audit remain release gates.


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

The production-only background dispatcher is now wired, and the fixed supervisor client runner is injected by `server.py` after successful startup recovery. The independent deployment opt-in remains disabled on the VPS; the persisted operator switch defaults off. The HTTP API exposes no direct model-launch endpoint: a batch must be explicitly assembled and approved before the dispatcher can select its jobs.


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

The legacy `runtime_subprocess.LlamafileSmokeRunner` remains an unwired prototype and is not the production runner. The wired path uses the privileged supervisor, a separate runtime UID, transient systemd unit/cgroup, namespace and filesystem restrictions, resource limits, immutable artifact handoff, and AF_UNIX bridge. These controls have code and disposable-container evidence, but the production supervisor service/bundle are not installed on the VPS; actual host provisioning, internal security audit and a separately approved real-model E2E remain release gates.
