# Hugging Face candidate catalog — owner-directed only

## Current policy (2026-11-19)

Automatic discovery of new Hugging Face repositories is disabled and must not be reintroduced without an explicit owner request.

- There is no scheduled or manually dispatchable GitHub Actions workflow that searches the public Hugging Face listing for new candidates.
- The former workflow `.github/workflows/huggingface-candidate-catalog-sync.yml` has been retired.
- The existing candidate manifest is a record of previously collected entries; its presence does not authorize adding, reviewing, downloading, running, or admitting those models.
- A new repository may be added only after the owner explicitly names or requests that model/repository.
- Metadata refresh automation is limited to the exact repositories already pinned as model IDs 1–8, through `.github/workflows/huggingface-model-catalog-sync.yml` and `existing_model_metadata_check.py`.
- The metadata checker must never call public listing/search endpoints or discover additional repositories.

## Admission and safety boundaries

- Candidate metadata is not the tournament admission allowlist.
- No license, technical readiness, or tournament admission is approved automatically.
- No model weights, tokenizer files, datasets, or binaries are downloaded by catalog metadata checks; no inference or training is run.
- No production database, active tournament, roster, model file, pinned manifest, or allowlist is changed by this catalog policy.
- Excluded models remain excluded and must not be restored by a metadata refresh or catalog import.

## Historical artifacts

Previously collected catalog entries and archived snapshots are retained as historical review data. They are not an instruction to continue discovery and are not automatically promoted into the active roster.
