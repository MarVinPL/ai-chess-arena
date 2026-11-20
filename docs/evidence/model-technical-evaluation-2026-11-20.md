# Model technical readiness evidence — 2026-11-20

## Scope and decision

This record documents bounded technical smoke evaluations of the exact Qwen2.5 artifact now pinned by model ID 1 and the exact Qwen2 artifact pinned by model ID 3 in `manifests/huggingface-models.json`. It does not grant model admission, change a roster, start a tournament, or authorize production use.

- Model: `Qwen/Qwen2-0.5B-Instruct-GGUF`
- Revision: `198f08841147e5196a6a69bd0053690fb1fd3857`
- File: `qwen2-0_5b-instruct-q4_k_m.gguf`
- Size: `397805248` bytes
- SHA-256: `f0a42bb979ca62b5e61f3bf924ab4b6a40aa091825ee7dcb4039949980ab81a8`
- Runtime: existing VPS llamafile v0.10.6, CPU-only, two threads, context 1024
- Runner: `LlamafileRuntime` from the isolated Issue #188 E2E workspace; `EliminationBoard`; temperature 0.0; maximum 16 generated tokens
- Execution: one model process, one request at a time, loopback API only, transient systemd scope configured with `MemoryMax=1200M` and `CPUQuota=150%`
- The transient scope control values were independently observed on the host cgroup as `memory.max=1258291200` and `cpu.max=150000 100000`.

## Results

| Position | Returned UCI | Legal | Elapsed |
|---|---:|---:|---:|
| Initial position | `b1c3` | PASS | 7.0211 s |
| Open game after 1. e4 e5 2. Nf3 Nc6 | `f3g5` | PASS | 4.0306 s |
| Nimzo-Indian position | `f3g5` | PASS | 4.8183 s |

All three responses were valid legal UCI moves on the first attempt, with no truncation and no retry. The local file size and SHA-256 match the exact manifest artifact.

## Technical disposition

- Model ID 3: `technical_readiness=ready`, `technical_blockers=[]`.
- Admission status deliberately remains `review_required`; `admission_scope` remains empty. No tournament or production permission is granted.
- The allowlist validator now treats technical readiness independently from admission and licensing. `tournament_admission_blockers` remains fail-closed and still requires an explicit admission decision, exact version, licence verification, and tournament scope.

## Qwen2.5 exact artifact (model ID 1)

- Model: `Qwen/Qwen2.5-0.5B-Instruct-GGUF`
- Revision: `9217f5db79a29953eb74d5343926648285ec7e67`
- File: `qwen2.5-0.5b-instruct-q2_k.gguf`
- Size: `415182688` bytes
- SHA-256: `9ee36184e616dfc76df4f5dd66f908dbde6979524ae36e6cefb67f532f798cb8`
- Artifact identity: official repository tree at the pinned revision contains the Q2_K file; its published SHA-256 matches the local file exactly. The existing Apache-2.0 audit applies to the same repository revision and its LICENSE/README.
- Runtime: existing VPS llamafile v0.10.6, CPU-only, two threads, context 1024
- Runner: isolated Issue #188 E2E workspace; `EliminationBoard`; temperature 0.0; maximum 16 generated tokens
- Execution: one model process, one request at a time, loopback API only, transient systemd service with `MemoryMax=1200M` and `CPUQuota=150%`; peak memory observed by systemd: 410.2 MiB

| Position | Returned UCI | Legal | Elapsed |
|---|---:|---:|---:|
| Initial position | `g1h3` | PASS | 47.5465 s |
| Open game after 1. e4 e5 2. Nf3 Nc6 | `f3g5` | PASS | 25.9705 s |
| Nimzo-Indian position | `c3e4` | PASS | 30.9220 s |

All three responses were valid legal UCI moves on the first attempt, without truncation or retry. This is a limited technical smoke test, not a chess-strength evaluation or a full match.

## Technical disposition update

- Model ID 1 Qwen2.5: `technical_readiness=ready`, `technical_blockers=[]` for the exact local Q2_K artifact now selected in the manifest.
- Model ID 3 Qwen2: `technical_readiness=ready`, `technical_blockers=[]` for the exact Q4_K_M artifact.
- Both models remain `status=review_required`, with empty `admission_scope`; neither is admitted to any tournament.

## Other installed models: evidence identity mismatch

The VPS also contains a SmolLM2 GGUF artifact that does not match the exact file selected by the current manifest:

- SmolLM2 local Q2_K: SHA-256 `d13c8b95c170f16175bbd4e28ba4bd8d23647039ac63be20d1eb0a26598fbdd3`, revision `10ac3ac5c618d6cdbe76c2845747feed4b5d71ac`; manifest selects Q4_K_M at revision `25eef036a8d245a5a188b1b45d4b240a39bc9f24`. Existing Q2_K smoke evidence cannot be transferred to Q4_K_M.

No model files were downloaded or changed. The unresolved SmolLM2 training-data provenance review remains open. Production service, database, roster, and active tournament state were not modified.
