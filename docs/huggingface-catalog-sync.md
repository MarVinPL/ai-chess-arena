# Hugging Face candidate catalog synchronization

## Scope and source

The scheduled GitHub Actions workflow queries only the public Hugging Face Hub model metadata API (`/api/models`) with the `gguf` filter. It requests repository IDs, commit SHA, timestamps, task/library metadata, tags, and sibling file names. It does not fetch README/model-card contents, file blobs, LFS metadata, or weights.

A candidate must be public, not disabled, tagged for `text-generation`, and contain at least one `.gguf` file other than helper/projector, imatrix, or draft files. Repositories tagged `adult`, `erotic`, `nsfw`, `porn`, `pornography`, `sexual`, `uncensored`, or `gore` are omitted from this catalog.

## Safety boundaries

- Candidate status is always informational: `unverified`, `unavailable`, `renamed`, or `not_matching`.
- Every entry has `review_required: true`.
- A `license_tag` is copied from public metadata only. It is not a license audit or approval.
- No candidate is inserted into the Arena database, allowlist, tournament roster, or runtime cache.
- No model weights are downloaded and no inference is started.
- The curated pinned manifest `manifests/huggingface-models.json` remains independent and unchanged by this job.

## History and failure behavior

The candidate manifest records the first detection time, the last verification time for changed metadata, the pinned commit revision, and a per-repository event history. Revision changes and metadata changes create history events. Renames retain the old record and create a new unverified record linked with `renamed_from`/`renamed_to`.

The search is bounded to 20 pages of 100 records. If the API advertises another page after that bound, the scan is marked partial. Absence from a bounded search is never treated as deletion: every previously known repository not present in the search results is checked directly by repository ID, even on a partial scan. A 404 is treated as unavailable; redirects/canonical IDs are recorded as renames; repositories that no longer meet the candidate criteria are marked `not_matching`. Other HTTP/API failures abort the run. Direct checks are paced to keep anonymous API traffic modest.

The script builds the entire new payload in memory and writes it atomically only after successful API retrieval and reconciliation. Empty first-page responses, malformed metadata, rate limits, timeouts, and API errors do not overwrite the last good manifest. The workflow step summary is the execution log for the latest run. The manifest is changed only when candidate metadata or status changes, so a no-op weekly check does not create a PR.

## Schedule and review

`.github/workflows/huggingface-catalog-sync.yml` runs every Monday at 04:17 UTC and supports `workflow_dispatch`. It uses the public GitHub Actions runner and `GITHUB_TOKEN` with repository-content and pull-request permissions. The `peter-evans/create-pull-request` action creates or updates one automation branch and PR when the manifest changes. The PR is not auto-merged and requires human review.
