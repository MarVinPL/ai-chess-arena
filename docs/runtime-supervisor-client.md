# API-side supervisor client runner (Issue #157)

runtime_supervisor_client.py is an opt-in runner prototype for the existing
runtime_executor.execute_job(runner=...) injection point. It is not imported
or selected by server.py and does not change the fail-closed default.

## Protocol

The runner sends only the positive queue job ID to the supervisor:
- start request has a bounded 1800-second client timeout to cover a verified
  artifact copy; the IPC server keeps request-read timeout short and uses a
  separate bounded response timeout;
- start response must name the deterministic unit for that job and report
  lease_state=active plus unit_state=start_requested;
- health and smoke inference use only the job's AF_UNIX bridge path and the
  two allowlisted operations;
- cancellation and hard deadline are polled during health and inference;
- stop targets the same deterministic job ID. If a stop response is lost, the
  runner reads durable status and accepts success only when the lease is
  explicitly stopped for the expected unit.

The runner never sends executable paths, artifact paths, command arguments,
unit names, environment values, or systemd properties over IPC. It ignores
payload path fields; the privileged authority and handoff layer load and
verify the pinned artifact independently.

## Failure handling

- If start fails ambiguously, the runner queries durable lease status and
  requests stop only when the exact job lease exists.
- Timeout, cancellation, health failure, invalid inference response and
  successful smoke inference all pass through exact-unit stop.
- If stop cannot be confirmed, the runner raises and leaves the durable lease
  and handoff for supervisor recovery.
- The IPC status response is lease state, not a claim about live systemd
  ActiveState.
- The runner reports unit_state=start_requested; it does not claim that model
  inference has started until the bridge returns healthy and smoke inference
  succeeds.

The server-side response timeout is separate from the short request-read
timeout. The AF_UNIX bridge similarly keeps a short request-framing timeout
but allows the upstream health/inference response to run up to the systemd
job deadline. The runner's own remaining-time deadline and systemd RuntimeMaxSec
bound the operation; idle peers cannot hold the bridge before sending a request.

## Validation boundary

Tests inject fake supervisor and bridge calls. They cover success, health
cancellation, inference cancellation, ambiguous start reconciliation,
invalid unit identity, non-200/empty inference, stop uncertainty, and
invalid job IDs/digests/timeouts.

This runner is not wired into runtime_executor or a deployment profile.
Before selecting it, startup recovery in runtime_executor must be coordinated
with the privileged supervisor so the API cannot mark a running job failed
while leaving a unit active. The current client polls cancellation during
health and inference; a continuous supervisor-side revocation watcher is
still not implemented.

No socket, service, systemd unit, model process, inference or production state
was used.
