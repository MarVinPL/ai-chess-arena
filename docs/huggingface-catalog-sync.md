# Hugging Face metadata synchronization policy

As of 2026-11-19, automatic discovery of new Hugging Face candidates is disabled by owner decision (Issue #63).

## Permitted automation

The workflow `.github/workflows/huggingface-model-catalog-sync.yml` may run weekly or manually, but it is strictly limited to checking metadata for the eight exact repository IDs already pinned as model IDs 1–8 in `manifests/huggingface-models.json`.

- It queries only those eight exact repository endpoints.
- It does not query public model listings, search for new repositories, or add candidates.
- It writes metadata to `manifests/huggingface-existing-models-metadata.json`, separate from the candidate catalog and admission allowlist.
- Changed revisions, moved sources, or unavailable repositories require human review.
- It cannot approve licenses, technical readiness, or tournament admission.
- It does not download model weights or run inference/training.

## Prohibited automation

- No scheduled or manual GitHub Actions workflow may discover new public Hugging Face repositories.
- The retired `.github/workflows/huggingface-candidate-catalog-sync.yml` must not be restored without explicit owner approval.
- New candidate entries require an explicit owner request identifying the repository/model.
- No metadata sync may modify the pinned model manifest, tournament allowlist, roster, production database, or active tournament.

## Safety and failure handling

- API errors, rate limits, empty/malformed responses, or invalid manifests abort the metadata check; the last valid metadata remains intact.
- A draft PR may be created only for metadata changes to the already-pinned repositories. It is never merged automatically and requires human review.
- Excluded model identities remain excluded and must not be restored by any sync.
