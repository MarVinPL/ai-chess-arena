# AI Chess Arena

AI Chess Arena is a self-hosted environment for running chess tournaments between locally hosted language models. It provides a browser panel, a Python HTTP API, persistent tournament results, model cataloguing, on-demand GGUF management, and a sequential model runner.

## Components

- `server.py` — loopback HTTP API and web-panel backend.
- `index.html` — tournament, model-catalogue, storage, ranking, and live-status UI.
- `league.py` — SQLite persistence, tournament scheduling, game records, and Elo tables.
- `model_store.py` — isolated GGUF downloads, SHA-256 verification, licence metadata, and safe cleanup.
- `engine_runner.py` — sequential llamafile orchestration and chess-move validation.
- `passkey_test.py` — separate Passkey knowledge test harness used in tournament evaluation.
- `tests/` — regression tests using temporary databases and fake runtimes.
- `LEAGUE.md` — tournament rules and implementation notes.

## Requirements

- Python 3.11 or newer.
- `python-chess` (see `requirements.txt`).
- Optional for real model inference: a compatible `llamafile` binary and GGUF model files.

Install dependencies and run tests:

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
PYTHONPATH=. pytest -q tests
```

Start the local panel/API:

```bash
python server.py
```

The default backend listens on `127.0.0.1:8090`. Keep it bound to loopback; use an approved SSH port-forward for remote access. Model inference is expected on loopback port `8088`.

## Model and data isolation

- GGUF files are runtime assets and are **not stored in Git**. The Arena-owned directory is `managed-models/`.
- SQLite databases, logs, PID files, backups, virtual environments, and local configuration are excluded by `.gitignore`.
- Live diagnostics are persisted as JSON Lines in `logs/model-responses.jsonl` (model outputs) and `logs/recent-events.jsonl` (status/event history). The `/api/live` response restores the latest entries after a backend restart; the logs are local runtime data, excluded from Git, and compacted when they exceed 1 MiB.
- The shared `/home/debian/ai-agent/models` directory is outside the Arena's ownership boundary. Never scan, modify, delete, or use it as a fallback model directory.
- Cleanup may target only Arena-owned files explicitly marked `managed_copy=1`; it must verify that a file is not in use before removal.
- Model downloads require a pinned source, expected size, SHA-256, and explicit licence verification. Adding a model to the catalogue does not automatically download or run it.
- The runner handles one model process at a time. Tournament games have no hard response-time limit; inference time is recorded in results.

## Llamafile watchdog

- While a tournament runtime has a model loaded, a background watchdog checks its owned child process and the loopback health endpoint every 30 seconds.
- If that child exits or its endpoint becomes unhealthy, the runtime attempts to restart the same model from the Arena-managed model path and emits a bounded live event.
- An intentional `runtime.stop()` clears the desired model and disables automatic restart, so normal model switching and tournament cleanup are not undone.
- The watchdog never searches for, signals, or adopts unrelated Llamafile processes. It does not start an arbitrary model when the Arena is idle; a tournament/runtime must first select and validate a model.

## Security and operational notes

This project is a local prototype, not a hardened public service. Do not expose the API or model-runtime port directly to the Internet. Do not commit credentials, private keys, model binaries, local databases, or user runtime data.

No tournament is started automatically when a model is added. Real inference and model downloads are separate, explicit operations.

## Hugging Face manifests

- Pinned, metadata-only GGUF manifests for the ten catalog entries are stored in `manifests/huggingface-models.json`.
- Validate/import them explicitly with `model_manifest.load_manifest()` and `model_manifest.apply_manifest(league.DB)`; the importer updates existing registry versions atomically, records an audit trail in `metadata_json`, and never downloads weights.
- Import deliberately sets `license_verified=0` for these entries. A Hugging Face license tag or model-card declaration is evidence, not approval to download/use a model. Complete a separate human license review before changing that flag.
- The Bielik 7B entry declares `CC-BY-NC-4.0` (NonCommercial restriction). Llama 3.1/3.2 use Meta Community Licenses, not Apache-2.0. The Qwen3.8 entry retains the base-model license/acceptable-use conditions.
- Manifest entries 9–10 are explicitly excluded by Issue #71; their declared repository licenses do not authorize admission. The separate draft-Q8_0 head in entry 9 is also excluded from standalone model identity.
- No GGUF weights are committed or fetched by this workflow. Run `python -m unittest tests.test_model_manifest -v` to test the manifest importer.

## Tournament enrollment and on-demand model cache

- New tournaments enroll every active, non-excluded registry row, including entries with no version or incomplete manifest; those rows remain visible with explicit readiness blockers. The exact repositories in manifest entries 9–10 are deliberately excluded and cannot enter a new tournament.
- Round-robin scheduling uses the actual registry model IDs and never creates self-pairings. With `n` active entrants, the league schedule contains `n × (n − 1)` games.
- The read-only `GET /api/tournaments/{id}/preflight` endpoint reports each entrant's manifest, license, local-cache and runtime state. Missing local GGUF files are informational, not a start blocker.
- When a game actually needs a model, the runner downloads its target and optional draft into the private Arena cache only if the license is explicitly verified and the download URL is safe. Hugging Face downloads must use a full immutable commit revision. Size and SHA-256 are verified before the file is activated; failed downloads do not create results or remove entrants.
- A missing/incomplete manifest, unapproved license, unavailable runtime, insufficient disk space, or integrity failure remains a visible blocker. No automatic download is performed for unverified licenses.

### Versioned model admission register

- `manifests/model-allowlist.json` is the GitHub-sourced decision register. `main` is the sole source of truth; every change must be made on a dedicated branch and PR.
- The allowlist is informational and read-only for consumers. `eligible` and `restricted` are explicit review decisions, not proof of runtime readiness; `license_verified=1`, complete local files, and runtime checks remain separate requirements. The current entries remain `review_required` or `blocked` until an owner-approved PR changes them.
- `model_allowlist.load_allowlist()` validates each entry against the pinned model manifest, including repository, exact revision, license status, evidence references, and required restrictions. It never writes to the database or changes a tournament.
- `scripts/check_model_allowlist_sync.py` checks whether the checkout is exactly at `origin/main` and whether the allowlist file has local changes. It is read-only: it does not fetch, merge, reset, or overwrite anything. Refresh Git refs separately before running it.
- Use `python -m pytest -q tests/test_model_allowlist.py` to test validation, changes, withdrawals, and stale/dirty consumer detection.
