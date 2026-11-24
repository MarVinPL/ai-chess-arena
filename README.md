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
- Model artifacts are downloaded by the VPS directly from the upstream source after a separate explicit approval; they are not downloaded through a developer workstation and are never stored in Git. The VPS must use the reviewed immutable revision, verify expected size and SHA-256 before activation, and store files only in the Arena-owned `managed-models/` directory. `/home/debian/ai-agent/models` is outside the Arena boundary and must never be used as a source, destination, or fallback.
- A positive licence review is necessary for the download workflow but does not trigger a download. Each selected model/artifact requires separate approval; failed or incomplete source, revision, licence, size, or checksum verification must fail closed. Adding a model to the catalogue does not automatically download or run it.
- The runner handles one model process at a time. Tournament games have no hard response-time limit; inference time is recorded in results.
- Minimum model technical admission is based on successful runtime startup and the ability to produce legal chess moves through the Arena runner. Slow inference and weak-but-legal strategy are recorded, not rejection grounds; Passkey remains a separate three-question supplemental test. See [`docs/model-technical-admission-policy.md`](docs/model-technical-admission-policy.md).

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
- When a game needs a model, the runner may retrieve its target and optional draft into the private Arena cache only when that exact artifact has both a positive licence review and a separately recorded explicit download approval. Licence verification alone never authorizes or triggers retrieval. The VPS downloads directly from the upstream source using a full immutable commit revision; expected size and SHA-256 are verified on the VPS before activation. Failed downloads do not create results or remove entrants.
- A missing/incomplete manifest, unapproved licence or artifact download, unavailable runtime, insufficient disk space, or integrity failure remains a visible blocker. No automatic download is performed merely because a model is catalogued, licensed, selected, or needed by a game.

- Źródło decyzji allowlisty to **wyłącznie SQLite**: model_admission_decisions
  i model_versions (z model_admission_history dla audytu). Dopuszczenie dotyczy
  dokładnego repozytorium, rewizji i licencji; brak decyzji blokuje udział.
- Preflight turnieju, API decyzji i akcje audytowe czytają bazę. Ani czystość
  checkoutu Git, ani zgodność z origin/main nie są warunkiem dopuszczenia.
  Wersjonowanie kodu nadal wymaga PR do main.
### Versioned model admission register

- The allowlist is informational and read-only for consumers. `eligible` and `restricted` are explicit review decisions, not proof of runtime readiness. The licence review is governed by [`docs/model-license-qualification-policy.md`](docs/model-license-qualification-policy.md): it checks the exact model version and applicable binding terms, without requiring a full training-data audit as part of the licence decision. A positive licence result does not download weights, verify local-file integrity, prove runtime health, or automatically add a model to a tournament. Recorded restrictions and applicable age conditions must be respected; unresolved terms remain `review_required`.
- Use `python -m pytest -q tests/test_model_allowlist.py` to test validation, changes, withdrawals, and stale/dirty consumer detection.

### Weekly metadata check for existing Hugging Face models

The free GitHub Actions workflow `.github/workflows/huggingface-model-catalog-sync.yml` checks only the eight repositories already pinned as model IDs 1–8. It queries their exact public metadata endpoints and does not perform public model discovery or add new repositories. Results are stored separately in `manifests/huggingface-existing-models-metadata.json`; the existing candidate catalog, pinned model manifest, admission allowlist, database, and tournament state are not changed. Changed revisions or unavailable sources require human review. The workflow downloads no model artifacts, performs no inference, and creates a draft PR only when tracked metadata changes. See `docs/huggingface-catalog-sync.md`.

### Weekly public Hugging Face candidate catalog

The separate free GitHub Actions workflow `.github/workflows/huggingface-candidate-catalog-sync.yml` discovers public GGUF text-generation/conversational repositories and updates only the informational `manifests/huggingface-candidates.json` catalog. New candidates remain `review_required`, `license_verified=false`, `technical_readiness=not_evaluated`, and `admission_status=not_admitted`; no license or tournament admission is approved automatically. It downloads no model artifacts, runs no inference, and does not modify the pinned model manifest, admission allowlist, database, or tournament state. It creates a draft PR only when tracked catalog metadata changes. The original 14 November 2026 snapshot (436 entries from a partial scan) is preserved separately under `manifests/archive/` and is not used by the workflow. See `docs/huggingface-candidate-catalog.md`.

## Safe tournament stop and resume

**Operational rule (Issue #98): a tournament may be stopped at any time, but its complete, consistent state must be durably saved before the runtime is stopped. It must then be resumable from the exact saved point rather than restarted from the beginning.**

The implementation must preserve the tournament configuration and entrants, schedule and pending-game order, every game's status and complete move history/position, results, current side to move, round/game progress, and any runner state needed to resume safely. If stop is requested while a model is generating a move, the in-flight unvalidated response must not be committed as a move; resume from the last verified position and retry generation for that same position.

The checkpoint must be atomic, integrity-checked, and confirmed durable before stopping owned runtime processes. Stop/resume operations must be idempotent and prevent duplicate games, moves, results, or Elo updates. The panel/API must expose the stopped state and a resume action. Preserve existing tournament data and database compatibility; do not reset or discard existing state to implement this feature. All code changes belong on a dedicated branch and PR, with backup and regression testing before production deployment.
