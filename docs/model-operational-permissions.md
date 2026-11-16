# Model operational permission scopes — Issue #136

## Purpose and boundary

This module records explicit, version-pinned operational permissions. It is not a model downloader, runtime executor, tournament-admission system, or license approval system.

The registry supports:

- `TEST_ONLY`: a future, separately isolated technical test;
- `EVALUATION`: a future isolated evaluation, requiring an active `TEST_ONLY` grant for the exact same model ID, repository, revision, and GGUF SHA-256;
- `TOURNAMENT`: deliberately rejected by this registry. Official tournament admission remains governed by the versioned allowlist and the existing admission workflow.

A request is not an approval. New models receive no implicit grants. A grant is tied to the exact model ID, repository identifier, full 40-character revision SHA, and 64-character artifact SHA-256. Changing any of these values requires a new request and decision.

## HTTP API and authentication

The panel exposes the following authenticated routes:

- `GET /api/model-permissions`: returns permission requests and their event history.
- `POST /api/model-permissions/request`: creates a pending request. Requires model ID, repository, full revision, artifact SHA-256, scope, reason, and explicit `confirm: true`.
- `POST /api/model-permissions/decision`: applies `approve`, `reject`, or `revoke` to a request ID with explicit `confirm: true`. Reject and revoke require a reason.

All routes are behind the existing `arena_auth` signed-cookie session check. POST requests additionally require the configured, exact `Origin`. The actor recorded in the registry is taken from the authenticated session username; caller-supplied actor fields are not used.

This deployment currently has a single Arena login identity. The recorded actor therefore identifies the authenticated Arena account, not a distinct human operator or multi-user RBAC principal. Do not represent this as multi-operator attribution.

## Persistence and audit invariants

- A partial unique SQLite index allows at most one pending request for an exact model/repository/revision/artifact/scope identity.
- Decision updates use a status compare-and-set so only one competing decision can win.
- Event history is append-only at the SQLite layer: triggers reject UPDATE and DELETE against the event table.
- Existing pre-digest development schemas are migrated by adding the artifact SHA-256 columns before creating the partial unique index.
- `EVALUATION` requests and approvals require active `TEST_ONLY` for the same exact identity.
- `TEST_ONLY` cannot be revoked while a matching `EVALUATION` grant remains active.

The event-table triggers protect against ordinary application-level mutation. They are not tamper-proof against a database owner who can alter the schema or drop triggers; independent/off-host audit retention is not part of this PR.

## Explicitly out of scope

This PR does not:

- download, copy, install, load, or execute model weights;
- invoke `systemd-run`, create workers, or change systemd configuration;
- enable inference, evaluation jobs, or tournaments;
- modify the model allowlist, tournament admission, roster, official games, results, or rankings;
- provide multi-operator RBAC, an independent identity provider, or an off-host tamper-evident audit service.

The permission registry is not yet consumed by a runtime executor. Any future executor must independently validate the exact permission and artifact fingerprint immediately before execution, enforce isolation and resource limits, and keep evaluation output separate from official tournament data. That work belongs to the separately tracked executor scope in Issue #157 and requires its own review and explicit authorization.
