# Issue #71 — deferred catalog entries 9–10

The following exact Hugging Face repositories are excluded from model admission:

- Manifest model ID 9: `JonathanColetti/Qwen3.8-27B-Uncensored-GGUF`
- Manifest model ID 10: `mradermacher/JonathanColetti-Qwen3.8-27B-Uncensored-GGUF`

## Decision boundary

The owner deferred the substantive decision to a later, separate PR. This change
therefore does **not** rewrite either entry's license review status, registry
`status`, source, pinned revision, filename, SHA-256, size, or metadata. The
existing values remain exactly as they were before this change.

## Enforced technical boundary

Until that later decision, the exact repositories above are excluded from:

- new tournament rosters;
- execution of any pre-existing tournament roster that contains either entry;
- direct and on-demand target/draft downloads;
- license-evidence collection and license-verification mutation;
- model runtime path resolution;
- model admission checks, including the shared predicate
  `model_policy.is_model_admission_allowed` for the upcoming allowlist consumer.

The model registry continues to display the records and reports the exclusion
reason separately from their existing status fields. Historical tournament
entries are not deleted or rewritten; preflight blocks their execution.

The GitHub-sourced allowlist is a separate follow-up (Issue #62) and does not yet
exist in this revision. Its consumer must use the shared admission predicate;
adding an allowlist entry alone must never override this exclusion.

No model weights are downloaded, no inference is started, no production database
or tournament state is modified, and no service is deployed or restarted by this
change.
