# Model licence qualification policy

Issue: #142

## Purpose

Keep model qualification proportionate. The licence review answers one question: whether the exact model version and intended Arena use are permitted by the applicable licence and binding use terms. It is not a general research audit of every training dataset, nor a technical readiness test.

## 1. Scope of a licence review

Review the exact tuple:

- upstream repository and immutable commit/revision;
- model identity and any declared base model;
- the exact artefact being considered (including quantized or merged derivatives);
- the licence text attached to that version;
- any separate terms, acceptable-use policy, or model-specific conditions that are binding on the intended use.

A repository tag, model-card statement, or filename is evidence to investigate, not by itself a completed review. If the exact applicable terms cannot be established, the result remains `review_required`.

## 2. Review outcomes

Use one of these outcomes:

- **verified_permissive** — the applicable licence and binding terms were checked for the exact version and no material restriction prevents the stated Arena use.
- **verified_restricted** — the exact terms were checked; the use is potentially permitted only if recorded obligations and restrictions are respected (for example attribution, notice retention, NonCommercial limits, or distribution conditions).
- **review_required** — source, version, licence, terms, or their applicability remain uncertain.
- **blocked** — the exact intended use is prohibited by an applicable term, or a required condition cannot be met.

Record source URLs or repository paths, immutable revisions, review date, reviewer, intended use, and concise evidence for the decision. Do not silently promote an uncertain result.

## 3. Age and content conditions

Do not infer a contractual age restriction solely from a content label, model nickname, category, or general-purpose safety classification. Check whether the exact licence or a separate binding term actually establishes an age requirement and whether it applies to the intended user and use.

If an applicable term expressly requires users to be 18 or older, the Arena must not treat that condition as waived or bypass it. A permissive software/model licence does not cancel a separate binding age condition. Where applicability is unclear, retain `review_required` and seek authoritative clarification; do not assume the condition is absent.

## 4. What a positive licence review means

A positive licence review is the licensing gate only. It may set the licence status to `verified_permissive` or `verified_restricted`, with obligations recorded.

It does **not** by itself:

- download or install model weights;
- authorize redistribution beyond the licence;
- prove the local file's integrity;
- prove runtime compatibility or health;
- add the model to a tournament roster;
- change `admission_scope` or grant an unrelated operational permission;
- start, resume, or modify a tournament.

The project may use the positive licence result as the primary legal qualification decision without requiring a full training-data provenance audit or broad technical audit as part of that licence review. Integrity and runtime checks remain separate operational controls. A tournament admission decision remains explicit and must respect the licence result, recorded restrictions, applicable age conditions, and the intended tournament format.

## 5. Proportionality and fail-closed rules

Do not block a model merely because its numeric model ID, a vague content label, or an unsubstantiated age assumption suggests a restriction. Evaluate the exact model identity and governing terms.

Do block or leave unresolved when:

- the repository or immutable revision does not match the reviewed evidence;
- the actual artefact is a derivative whose licence or upstream obligations are unknown;
- a separate binding term conflicts with the intended use;
- a required obligation cannot be met;
- the terms cannot be obtained or interpreted with sufficient confidence.

Do not treat this policy as a blanket permission for any particular model. Existing allowlist decisions remain unchanged until separately reviewed and approved.

## 6. VPS-hosted model artifact retrieval

When a model download is separately approved, the VPS is the download host: it retrieves the selected artifact directly from the upstream source. Model weights are not routed through a developer workstation and are not committed to Git.

The VPS-side retrieval workflow must:

- use the exact repository and immutable revision covered by the licence review;
- identify the exact artifact/variant and record its expected size and SHA-256 before activation;
- verify the downloaded file's size and SHA-256 on the VPS before making it available to the Arena runtime;
- store the file only in the Arena-owned `managed-models/` area, with ownership and permissions limited to the Arena workflow;
- never read from, copy to, modify, or use `/home/debian/ai-agent/models` as a source or fallback;
- record the source, revision, artifact identity, verification result, and destination in the Arena's audit metadata.

A positive licence review does not trigger a download. Each actual download requires a separate explicit approval for the selected model and artifact. The VPS workflow must fail closed if the pinned revision, artifact identity, expected size, checksum, licence status, or required terms cannot be verified. Downloading does not itself authorize inference or tournament admission.

## 7. Operational boundaries for this policy change

This document does not authorize model downloads, inference, roster changes, database writes, tournament changes, VPS deployment, or service restarts. Any such action requires its own approved workflow and evidence. The VPS-hosted retrieval design above describes where an approved download is performed; it is not permission to execute one as part of this documentation change.
