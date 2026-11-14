# Weekly Hugging Face candidate catalog sync

The free GitHub Actions workflow `.github/workflows/huggingface-model-catalog-sync.yml` runs every Monday at 04:17 UTC and supports manual dispatch. It uses the public Hugging Face model metadata API and the standard GitHub Actions token; no paid service is required.

## Discovery and verification scope

- The sync discovers public repositories tagged for GGUF and text-generation/conversational tasks using the Hugging Face listing API (sorted by recent modification and downloads).
- It also verifies the exact repository endpoints for the eight pinned model IDs 1–8 in `manifests/huggingface-models.json`.
- Model IDs 9–10 are not admitted or enabled by this workflow; they remain separately excluded under Issue #71.
- Candidate metadata is stored in `manifests/huggingface-candidates.json`. The catalog is informational and is not the tournament admission allowlist.
- Repository/tag exclusions are applied conservatively and fail-closed, including the shared model identity exclusion rules.

## Review and change handling

- Newly discovered repositories are recorded as `review_required`, `license_verified=false`, `technical_readiness=not_evaluated`, and `admission_status=not_admitted`.
- Existing pinned repositories retain their separate identity and audit history. Changed revisions invalidate prior license review; missing, moved, gated, private, disabled, or excluded sources are recorded for human review or blocked as appropriate.
- A draft PR is opened only when tracked catalog metadata changes. It is never merged automatically and requires human review.
- API errors, rate limits, empty/malformed responses, or invalid manifests abort the update. The previous catalog is preserved and an error report is written for the Actions artifact.
- The workflow does not approve licenses, technical readiness, or tournament admission, and does not alter the admission allowlist.

## Safety boundaries

- Metadata only: no model weights, tokenizer files, datasets, or model binaries are downloaded; no inference is run.
- No production database, active tournament, roster, model file, or allowlist is changed.
- The workflow runs on GitHub-hosted runners and does not access or modify the VPS.

## Local validation

```bash
python -m pytest -q tests/test_huggingface_catalog_sync.py
python -m pytest -q tests/
python scripts/sync_huggingface_catalog.py
```
