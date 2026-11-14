# Weekly Hugging Face candidate catalog sync

The free GitHub Actions workflow `.github/workflows/huggingface-candidate-catalog-sync.yml` runs every Monday at 04:17 UTC and supports manual dispatch. It uses the public Hugging Face model metadata API and the standard GitHub Actions token; no paid service is required.

## Discovery and verification scope

- The workflow discovers public repositories tagged for GGUF and text-generation/conversational tasks using the Hugging Face listing API, ordered by recent modification and downloads.
- It reconciles exact repository endpoints for the eight pinned model IDs 1–8 and known candidate repositories so source changes, redirects, and unavailable sources can be recorded. The dedicated workflow `.github/workflows/huggingface-model-catalog-sync.yml` separately checks only the eight pinned repositories and writes to `manifests/huggingface-existing-models-metadata.json`.
- Model IDs 9–10 remain separately excluded under Issue #71; candidate discovery does not admit them.
- Candidate metadata is stored in `manifests/huggingface-candidates.json`. This catalog is informational and is not the tournament admission allowlist. The tracked catalog is capped at 500 repositories.
- Repository/tag exclusions are applied conservatively and fail-closed, including the shared model identity exclusion rules.

## Review and change handling

- Newly discovered repositories remain `review_required`, `license_verified=false`, `technical_readiness=not_evaluated`, and `admission_status=not_admitted`.
- Changed revisions invalidate prior license review. Missing, moved, gated, private, disabled, or excluded sources are recorded for human review or blocked as appropriate.
- A draft PR is opened only when tracked catalog metadata changes. It is never merged automatically and requires human review.
- API errors, rate limits, malformed responses, or invalid manifests abort the update without replacing the last valid catalog.
- The workflow does not approve licenses, technical readiness, or tournament admission, and does not alter the admission allowlist.

## Safety boundaries

- Metadata only: no model weights, tokenizer files, datasets, or model binaries are downloaded; no inference is run.
- No production database, active tournament, roster, model file, pinned manifest, or allowlist is changed.
- The workflow runs on GitHub-hosted runners and does not access or modify the VPS.
- The original 14 November 2026 PR #91 snapshot is preserved at `manifests/archive/huggingface-candidates-legacy-20261114.json`. It contains 436 unique entries from a bounded, partial scan and is archival evidence only; the scheduled workflow does not read or update it. The active catalog remains `manifests/huggingface-candidates.json`.

## Local validation

```bash
python -m pytest -q tests/test_huggingface_catalog_sync.py
python -m pytest -q tests/test_existing_model_metadata_check.py
python scripts/sync_huggingface_catalog.py
```
