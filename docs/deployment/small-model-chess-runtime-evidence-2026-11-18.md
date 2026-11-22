# Small-model chess runtime evaluation — 2026-11-18

## Decision

The owner accepted both exact tested artifacts as **technically playable candidates** under the minimum criterion: the pinned artifact loads in the Arena llamafile runtime and the Arena runner obtains a legal move in each of three representative positions. Slow inference and weak-but-legal strategy are informational only and are not rejection grounds.

This is a technical evaluation of the exact artifact identities below. It is **not** tournament admission, a roster/allowlist change, a production installation, or a grant to run future inference. Existing licence, provenance, managed-artifact, resource-preflight, and explicit operational-approval gates remain independent.

## Test method

- Existing VPS only; no new VM/container/image was created.
- One model process at a time, in a transient systemd unit with a per-process cgroup limit; the production `ai-chess-arena.service` remained active.
- The evaluation used the repository's `engine_runner.LlamafileRuntime` and `python-chess` board objects. Each request supplied the current FEN and Arena-generated legal-move list; the runner parsed the model output and validated the resulting move against the board's legal moves.
- Three positions: initial position; after `1. e4 e5 2. Nf3 Nc6`; and a Nimzo-Indian position after `1. d4 Nf6 2. c4 e6 3. Nc3 Bb4`.
- Temperature 0.0, maximum 16 generated tokens per request, two inference threads, context 1024. No move timeout was used as an admission gate.
- The test is a three-position smoke evaluation, not a strength/Elo benchmark or a complete tournament.

## Results

| Candidate | Position | Returned UCI | Legal | Elapsed |
|---|---|---:|---:|---:|
| SmolLM2 360M Q2_K | Initial | `g1h3` | PASS | 104.2931 s |
| SmolLM2 360M Q2_K | Open game | `f3g5` | PASS | 85.6942 s |
| SmolLM2 360M Q2_K | Nimzo-Indian | `g1h3` | PASS | 92.4905 s |
| Qwen2.5 0.5B Q2_K | Initial | `b1c3` | PASS | 93.6017 s |
| Qwen2.5 0.5B Q2_K | Open game | `f3g5` | PASS | 84.9042 s |
| Qwen2.5 0.5B Q2_K | Nimzo-Indian | `g1h3` | PASS | 88.7937 s |

Both candidates returned a legal move on all three positions, on the first runner attempt. SmolLM2 sometimes emitted a longer comma-separated continuation; the Arena runner extracted and validated the first move. Qwen returned a single UCI move in each case. Move quality was not used as a pass/fail condition.

## Exact artifact identities

### SmolLM2 360M Instruct Q2_K

- Repository: `QuantFactory/SmolLM2-360M-Instruct-GGUF`
- Revision: `10ac3ac5c618d6cdbe76c2845747feed4b5d71ac`
- File: `SmolLM2-360M-Instruct.Q2_K.gguf`
- Size: `218673792` bytes
- SHA-256: `d13c8b95c170f16175bbd4e28ba4bd8d23647039ac63be20d1eb0a26598fbdd3`
- Isolated systemd unit: `arena-smollm2-arena-chess-smoke-20261118b.service`
- systemd-reported memory peak: `123.4M`
- Unit completed successfully and was collected.

### Qwen2.5 0.5B Instruct Q2_K

- Repository: `Qwen/Qwen2.5-0.5B-Instruct-GGUF`
- Revision: `9217f5db79a29953eb74d5343926648285ec7e67`
- File: `qwen2.5-0.5b-instruct-q2_k.gguf`
- Size: `415182688` bytes
- SHA-256: `9ee36184e616dfc76df4f5dd66f908dbde6979524ae36e6cefb67f532f798cb8`
- Isolated systemd unit: `arena-qwen25-arena-chess-smoke-20261118.service`
- systemd-reported memory peak: `111.3M`
- Unit completed successfully and was collected.

The local Qwen file size and SHA-256 were recomputed on the VPS and matched the pinned metadata. The SmolLM2 local file's size and SHA-256 were also verified before execution.

## Operational disposition

- Both exact tested artifacts: **technical chess-playability PASS; owner accepted**.
- Production API: remained active; no restart or configuration change.
- Test processes: exited successfully; transient units were collected; no model process remained.
- No production database, tournament, roster, model allowlist, managed-model directory, or production runtime was changed.
- Test weights remain in the temporary evaluation directory `/var/tmp/arena-model-evaluation-20261118`; they were not copied into the Arena-managed model store.
- Passkey remains a separate supplemental evaluation (three questions, no response timeout, elapsed time recorded); this chess smoke test does not claim a completed Passkey result.

## Identity and admission caveats

The tested SmolLM2 revision `10ac3ac5c618d6cdbe76c2845747feed4b5d71ac` differs from the SmolLM2 revision currently present in the checked-in model allowlist (`25eef036a8d245a5a188b1b45d4b240a39bc9f24`). The technical PASS applies only to the tested revision; it must not be copied to the other revision.

The tested Qwen2.5 revision matches the pinned revision in the existing allowlist. Its technical test evidence does not change the existing `review_required` admission state or grant tournament permission.

No allowlist, manifest, model database, or roster entries are changed by this evidence report. Any future tournament admission must follow the existing explicit admission workflow and all independent safeguards.
