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

## Security and operational notes

This project is a local prototype, not a hardened public service. Do not expose the API or model-runtime port directly to the Internet. Do not commit credentials, private keys, model binaries, local databases, or user runtime data.

No tournament is started automatically when a model is added. Real inference and model downloads are separate, explicit operations.
