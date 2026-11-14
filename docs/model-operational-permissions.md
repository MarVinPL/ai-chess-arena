# Model operational permission scopes (Issue #136)

`admission_scope` in `manifests/model-allowlist.json` continues to mean tournament format (`PURE_AI` / `AI_ENGINE`). It is not an operational permission.

## Operational scopes

- `TEST_ONLY`: permission for a future isolated technical test. Does not grant download, redistribution, or execution consent.
- `EVALUATION`: permission for future isolated benchmarks/test matches. Requires a separately approved `TEST_ONLY` permission for the exact same model ID, repository, and full revision SHA.
- `TOURNAMENT`: deliberately not grantable in this registry. Official admission remains governed by the versioned allowlist and existing tournament preflight.

A request is not an approval. All new model versions start with no operational permissions. Every request and approval is pinned to the exact model ID, repository, full revision SHA, and SHA-256 of one concrete GGUF artifact. The panel requires the artifact digest; a different file hash cannot inherit the grant. Decisions require explicit confirmation and create an append-only event history. Revoking `TEST_ONLY` is blocked while `EVALUATION` remains active.

## Existing production isolation baseline

The deployed `ai-chess-arena.service` already provides a service-level systemd boundary: `NoNewPrivileges`, `PrivateTmp`, `PrivateDevices`, `ProtectSystem=full`, `ProtectHome=tmpfs` with explicit read-only binds for the fixed worker executable and selected artifact, kernel hardening, restricted address families, `KillMode=control-group`, and an explicit inaccessible path for `/home/debian/ai-agent/models`. The model-test design reuses this existing baseline; it does not create or modify another always-on sandbox service.

This service-level boundary is not, by itself, a per-job sandbox. A model job needs a separate transient systemd service with its own dynamic UID, private runtime directory, read-only bind of exactly one Arena-managed GGUF, no network, and per-job cgroup limits.

## Per-job sandbox profile (builder only)

`model_sandbox.py` implements a pure, non-executing profile builder:

- accepts only a regular `.gguf` file resolved inside the Arena-owned `managed-models/` directory; symlink escapes and files outside that root are rejected;
- explicitly rejects the shared `/home/debian/ai-agent/models` path;
- creates a shell-free `systemd-run --system --wait --pipe --collect` argv for a transient service;
- uses `DynamicUser=yes`, a unique private `RuntimeDirectory`, `PrivateNetwork=yes`, `RestrictAddressFamilies=AF_UNIX`, `ProtectSystem=strict`, `ProtectHome=tmpfs`, `NoNewPrivileges=yes`, `PrivateTmp=yes`, `PrivateDevices=yes`, and `KillMode=control-group`;
- binds only the fixed worker executable and selected model artifact read-only, hides the rest of host home directories, and makes the shared model directory inaccessible;
- bounds CPU quota (25–800%), memory (512 MiB–8 GiB), runtime (10–1800 seconds), and task count (1–128);
- computes SHA-256 and byte size by streaming the selected artifact without changing it.

The default profile is 100% of one CPU, 2 GiB memory, 600 seconds and 32 tasks. Limits are policy bounds, not a claim that the host has these resources available; an execution broker must additionally check current host capacity before starting a job.

**Important:** this PR slice only validates inputs, fingerprints an existing managed artifact, and builds argv. It does not invoke `systemd-run`, execute a model, download weights, or modify production systemd configuration. The argv builder is not an authorization boundary by itself. Before enabling execution, a privileged broker must be separately implemented and reviewed so untrusted HTTP input cannot choose arbitrary worker commands or systemd properties. It must independently re-check current permission, exact repository/revision, artifact hash, one-operation consent, host capacity, and job ownership.

## Safety boundary

The permission registry and sandbox profile builder do not download, install, load, or execute models. They do not modify `model-allowlist.json`, `admission_scope`, license review, technical readiness, tournament entries, games, ratings, or tournament state. Official results and rankings remain outside the test/evaluation data path.

No model is automatically granted a scope. A model download remains a separate operation requiring its own approval, source/license review, and artifact verification. Original weights remain untouched; tests may only reference a verified Arena-managed copy.

## Remaining execution integration

Still required before a real model test can run:

1. a narrowly privileged execution broker with a fixed, reviewed worker command (no arbitrary command supplied by the panel);
2. authenticated actor propagation from the existing panel session (the current actor argument is not authentication);
3. an installed and independently reviewed worker executable;
4. isolated evaluation output storage and proof that no writes can reach official tournament/result/rating tables;
5. a real systemd stop adapter, timeout/process-tree cleanup and a controlled capacity probe;
6. integration tests against a disposable systemd test unit, plus security review of the broker's privilege boundary.

## Database

`model_permissions.ensure_schema(connection)` creates only the two permission audit tables. It is safe to call repeatedly. No existing model or tournament records are backfilled or promoted.


## Transactional job ledger and pre-start authorization (implementation slice)

`model_job_broker.py` adds a separate job ledger and evaluation-metrics table. It is deliberately not wired to an HTTP endpoint or a privileged service yet.

- Job creation requires an active `TEST_ONLY` or `EVALUATION` grant pinned to the exact artifact SHA-256, explicit one-operation confirmation, a matching file fingerprint, and a positive host-capacity callback.
- Immediately before a start plan is issued, the ledger rechecks the active scope, artifact SHA-256 and byte size, approved limits, and current host-capacity callback.
- The one-operation consent is consumed atomically with the queued → starting transition. A second claim is rejected.
- The worker executable is a fixed constant; panel/API input cannot supply a command, systemd property, or arbitrary argument vector.
- The worker output directory is constrained to the job's private systemd runtime directory.
- Job lifecycle events and evaluation metrics are stored in separate tables. Metric writes are accepted only for `EVALUATION` jobs; no official tournament/result/rating table is referenced.
- Start-plan generation is side-effect free: it returns an argv list and explicitly reports `side_effects_performed=false`. It does not call `systemd-run`, create a process, or claim that a job is running.

**Not yet implemented:** a privileged launcher that accepts only these validated plans, authenticated actor propagation from the existing panel session, cancellation of a live systemd unit, stale-job recovery, worker executable/package, and disposable-systemd integration tests. Until those exist and receive security review, no real model job can be started through this code. The actor parameter is an interface contract for a future trusted caller, not proof of authentication in this slice.


## Cancellation and restart reconciliation (ledger only)

The job ledger now supports:
- cancelling a queued job before a systemd unit exists;
- recording a cancellation request for a starting/running job without claiming it has stopped;
- transitioning an active job to `cancelled` only after an injected, authoritative systemd state reader confirms the exact recorded unit is inactive;
- reconciling a starting/running row after a service restart: an active or unknown unit leaves the row unchanged; a confirmed inactive unit becomes `failed`, or `cancelled` if cancellation had already been requested.

These functions do not call `systemctl`, `systemd-run`, or `systemd` D-Bus. They accept a state-reader callback so unit inspection and stop privileges remain outside the web/API process. The real stop adapter and its authorization boundary are still outstanding.
