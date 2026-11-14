# Tournament checkpoints and safe resume

Issue #98 implements safe tournament stop/resume without resetting the existing league database.

## Persistence model

- `tournament_checkpoints` stores one versioned checkpoint per tournament. The canonical JSON payload is protected by SHA-256 and written in a SQLite `BEGIN IMMEDIATE` transaction with `synchronous=FULL`.
- The payload captures the tournament configuration, entrants, complete pairing schedule, active pairing, both players, game start time, verified UCI move records, per-move timings, FEN and side to move.
- A checkpoint is written before a pairing is marked running, refreshed after that transition, and replaced after every verified move. A checkpoint is read back and its digest checked before it can be used.
- The runner reconstructs the board by replaying the saved UCI history from the standard initial position. Every move must be legal and the reconstructed FEN must exactly match the saved FEN. A mismatch fails closed and preserves the checkpoint for investigation.
- If stop is requested during model inference, the response is discarded before move validation/commit. Resume retries that same side to move from the last verified checkpoint.
- Passkey answers are committed individually. An answer that finishes after stop was requested is discarded; the unanswered question is selected again from the persisted answer history after resume.

## Idempotency and recovery

- A completed game's row, pairing status, draft winner (when applicable), and checkpoint deletion are committed together in one SQLite transaction.
- Failure/adjudication records use the same transactional boundary and remove the now-obsolete in-flight checkpoint.
- A process restart changes a `running` tournament to `stopped` only when a checkpoint exists. A legacy/incomplete `running` tournament without a checkpoint is marked `failed` with `resume_blocked=1`; the panel does not offer a resume action and the API refuses to restart it until its state is manually reviewed.
- A pairing left `running` is resumed before any pending pairing. A running pairing without a valid checkpoint blocks resume.
- Existing game rows are checked by `(tournament_id, pairing_id)` before insertion to prevent duplicate results.

## Validation

Run the complete suite with:

```bash
python -m pytest -q tests
```

The checkpoint-specific tests cover integrity, corruption, deletion idempotency, legal-history replay, FEN verification and discarding an inference result when stop arrives.
