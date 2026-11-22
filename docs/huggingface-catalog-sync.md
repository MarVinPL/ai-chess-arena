# Hugging Face metadata check for existing models

The free GitHub Actions workflow `.github/workflows/huggingface-model-catalog-sync.yml` runs every Monday at 04:17 UTC and can also be started manually. It uses only the public Hugging Face model metadata API and the standard GitHub Actions token. No paid service is required.

## Strict scope

- The check is limited to the eight repository IDs already present as model IDs 1–8 in `manifests/huggingface-models.json`.
- It queries each of those eight exact repository endpoints. It does not query the public model listing, search for new models, or add any new repository.
- Model IDs 9–10 are not included; they are managed separately under Issue #71.
- Results are stored separately in `manifests/huggingface-existing-models-metadata.json`. The existing candidate catalog is not overwritten or mixed with this check's schema.

## Change handling

- A matching source revision preserves the existing license-audit state.
- A changed revision, missing repository, moved repository, or private/disabled/gated source is retained with a history event and marked `review_required`.
- A changed revision invalidates the previous license-review status. The workflow cannot approve licenses, technical readiness, or tournament admission.
- An unchanged weekly check does not rewrite the metadata manifest and therefore does not create a PR.
- An API error, rate limit, empty/malformed response, or invalid manifest aborts the update. The previous manifest is preserved and the error report is uploaded as an Actions artifact.
- A draft PR is opened only when tracked metadata changes. It is never merged automatically and requires human review.

## Safety boundaries

- Metadata only: no weights, tokenizer files, datasets, or model binaries are downloaded; no inference is run.
- No public model discovery is performed.
- No production database, active tournament, roster, model file, pinned manifest, or admission allowlist is changed.
- The informational existing-model metadata manifest is not an admission decision.
