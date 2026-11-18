# Minimal technical admission criteria for chess models

This policy defines the minimum **technical chess-playability** threshold for a model candidate. It intentionally does not rank playing strength.

## Required minimum

A candidate passes the technical chess-playability check when all of the following are demonstrated on the existing Arena host:

1. The exact, integrity-verified model artifact can be loaded by the configured Arena runtime.
2. The runtime starts successfully, exposes its expected local API/health endpoint, and can accept an inference request without crashing or becoming unusable.
3. Given a valid chess position (FEN) and the Arena-provided legal-move list, the model/runner produces a move that the Arena chess rules implementation accepts as legal. The result must be a real move for the supplied position, not an empty response or unrelated text.
4. The test records the position/FEN, legal-move validation result, model artifact identity and digest, runtime outcome, and observed response time. A failed or malformed first response may be handled by the existing bounded runner retry; the final committed move must still be legal.

At minimum, test the initial position and representative non-initial positions. Use the same move-generation and validation path as the Arena runner wherever practical. Do not treat a free-form answer that merely mentions a chess move as proof of playability.

## Explicitly not rejection criteria

The following are measurements, not minimum admission gates, and must not by themselves disqualify a candidate:

- slow inference or long per-move response time;
- low Elo, weak strategy, or an unambitious but legal move;
- preference for a different legal move than a stronger engine would choose;
- limited language fluency, provided the model can follow the Arena's move-generation contract sufficiently to produce legal moves.

Record latency and observed move quality for information and later comparison. Do not impose a per-move timeout or a minimum strength/Elo threshold under this policy. A legal move need not be strategically optimal to satisfy the minimum chess-playability criterion.

## Passkey knowledge evaluation

Passkey is a separate supplemental knowledge test, not the primary chess-playability test and not a substitute for playing chess. During tournament evaluation, ask each model three Passkey questions, in Polish or English. Do not impose a response timeout; record each answer and its elapsed time. Passkey answers are reported separately and do not change whether the model meets the minimum chess-playability criterion.

## Separate mandatory safeguards

Passing this technical criterion does **not** waive any independent requirement for:

- exact artifact/revision identity and SHA-256 integrity verification;
- applicable licence and usage-term review;
- explicit authorization for download, runtime execution, and any production action;
- resource containment, process isolation, and runtime health monitoring;
- Arena runner validation of every committed move.

This policy does not automatically add a model to the official allowlist, tournament roster, or production runtime. Those changes remain subject to their existing review and approval workflow.

## Mandatory current-VPS runtime gate

The runtime target is the **currently configured VPS that will host AI Chess Arena**, using its actual available resources and the approved Arena runtime. A model is not technically ready merely because it runs on a developer workstation, another server, a theoretical hardware configuration, or in a mocked test.

Before attempting to start a candidate, perform a safe resource preflight against the current VPS (including available RAM/swap, disk space, and other relevant runtime constraints). Never infer compatibility or incompatibility from parameter count or GGUF file size alone.

Use these distinct outcomes and preserve evidence:

- **PASS** — the exact, integrity-verified model artifact was actually loaded and started on the current VPS, accepted a real inference request, and produced a move accepted as legal by the Arena chess rules.
- **FAIL** — an explicitly authorized real attempt on the current VPS demonstrated that the candidate cannot start or remain usable under the approved runtime. Record the exact failure and supporting logs/metadata.
- **SKIPPED_BLOCKED** — the preflight demonstrated that the candidate cannot safely fit within the current VPS resource budget. Do not launch it. Record the measured constraint and reason; this is an operational exclusion, not proof of a runtime crash.
- **NOT_VERIFIED** — no qualifying real test has been completed. Missing evidence, metadata-only review, a successful worker/API/supervisor test, or a dummy-model recovery drill must never be represented as PASS or FAIL.

A candidate marked **FAIL** or **SKIPPED_BLOCKED** is excluded from the active candidate pool, further comparative evaluation, and admission consideration for the current VPS. Retain its historical record and evidence. Do not retry it or return it to the active pool unless a material condition changes (for example, VPS resources, runtime compatibility, or the exact artifact) and a separate decision authorizes reconsideration. Continue with the next candidate where the test queue supports it.

A **NOT_VERIFIED** candidate is not yet passed and cannot be treated as ready, but must not be described as a confirmed runtime failure or silently relabelled as excluded due to failure. Any real download or model execution still requires the separate explicit authorization and safeguards described above.

The gate applies to every future candidate and every model-derived artifact proposed for use (including a fine-tuned model/adapter). Each candidate is evaluated independently. Passing the runtime gate does not automatically admit it to the allowlist, roster, or a tournament.
