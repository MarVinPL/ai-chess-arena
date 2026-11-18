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
