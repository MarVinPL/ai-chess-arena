# Issue #165 — upstream runtime rollback

## Selection rules

- If `ARENA_RUNTIME` is missing, blank, or unrecognized, the application constructs the established `llamafile` runtime on `127.0.0.1:8088`.
- Selecting upstream requires both `ARENA_RUNTIME=llama-server` and `ARENA_UPSTREAM_RUNTIME_ENABLED=1`.
- Selecting `llama-server` without the enable flag is a configuration error. It does not silently fall back to llamafile.
- Once upstream has been explicitly selected, binary, startup, health-check, or inference failures remain errors. There is no automatic runtime-to-runtime fallback.

## Rollback to the established runtime

1. Change the application environment to set `ARENA_RUNTIME=llamafile`.
2. Set `ARENA_UPSTREAM_RUNTIME_ENABLED=0` or remove that variable.
3. Apply the environment change through the separately approved release/deployment procedure so the application process reads the updated environment.
4. Confirm the application reports/uses the established llamafile endpoint on `127.0.0.1:8088`.
5. Keep any upstream binary and model files untouched; rollback does not require replacing the llamafile binary, moving model files, changing the database, or modifying the allowlist.

This document describes the configuration rollback only. It does not authorize a production environment change, service restart, binary installation, model execution, inference, tournament, or deployment. Those actions require their own explicit approval.
