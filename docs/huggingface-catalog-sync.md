# Hugging Face metadata check for existing models

The weekly workflow `.github/workflows/huggingface-model-catalog-sync.yml` runs every Monday at 04:17 UTC and can also be started manually. It uses only the public Hugging Face model metadata API and the standard GitHub Actions token. No paid service is required.

## Strict scope

- The check is limited to the eight repository IDs already present as model IDs 1–8 in `manifests/huggingface-models.json`.
- It queries each of those eight exact repository endpoints. It does not query the public model listing, search for new models, or add any new repository.
- Model IDs 9–10 are not included; they are managed separately under Issue #71.
- The informational check result is stored in `manifests/huggingface-candidates.json`. The admission allowlist is not modified.

## Change handling

- A matching source revision is recorded as unchanged metadata; the existing license audit state is preserved.
- A changed revision, missing repository, moved repository, or private/disabled/gated source is retained with a history event and marked `review_required`.
- A changed revision invalidates the previous license-review status. The workflow cannot approve licenses, technical readiness, or tournament admission.
- Check timestamps are recorded in the per-run report. An unchanged weekly check does not rewrite the manifest and therefore does not create a PR.
- An API error, rate limit, empty/malformed response, or invalid manifest aborts the update. The previous manifest is preserved and the error report is uploaded as an Actions artifact.
- A draft PR is opened only when tracked metadata changes. It is never merged automatically and requires human review.

## Safety boundaries

- Metadata only: no weights, tokenizer files, datasets, or model binaries are downloaded; no inference is run.
- No production database, active tournament, roster, model file, or allowlist is changed.
- The workflow runs on GitHub-hosted runners; it does not access or modify the VPS.

## Local validation

```bash
python -m pytest -q tests/test_huggingface_catalog_sync.py
python -m pytest -q tests/
python scripts/sync_huggingface_catalog.py
```
