# Model operational permission scopes (Issue #136)

`admission_scope` in `manifests/model-allowlist.json` continues to mean tournament format (`PURE_AI` / `AI_ENGINE`). It is not an operational permission.

## Operational scopes

- `TEST_ONLY`: permission for a future isolated technical test. Does not grant download, redistribution, or execution consent.
- `EVALUATION`: permission for future isolated benchmarks/test matches. Requires a separately approved `TEST_ONLY` permission for the exact same model ID, repository, and full revision SHA.
- `TOURNAMENT`: deliberately not grantable in this registry. Official admission remains governed by the versioned allowlist and existing tournament preflight.

A request is not an approval. All new model versions start with no operational permissions. Decisions are version-pinned, require explicit confirmation, and create an append-only event history. Revoking `TEST_ONLY` is blocked while `EVALUATION` remains active.

## Safety boundary

The permission registry does not download, install, load, or execute models. It does not modify `model-allowlist.json`, `admission_scope`, license review, technical readiness, tournament entries, games, ratings, or tournament state. A later execution workflow must independently require a current exact-version permission, a separate per-operation consent, a verified artifact hash, isolated storage/runtime, resource/time limits, and cleanup. This PR does not enable model execution.

## Database

`model_permissions.ensure_schema(connection)` creates only the two permission audit tables. It is safe to call repeatedly. No existing model or tournament records are backfilled or promoted.
