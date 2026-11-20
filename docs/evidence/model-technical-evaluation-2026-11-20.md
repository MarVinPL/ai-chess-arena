# Model technical readiness evidence — 2026-11-20

## Scope and decision

This record documents a bounded technical smoke evaluation of the exact Qwen2 artifact pinned by model ID 3 in `manifests/huggingface-models.json`. It does not grant model admission, change a roster, start a tournament, or authorize production use.

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

## Other installed models: evidence identity mismatch

The VPS also contains two other GGUF artifacts, but they do not match the exact files selected by the current manifest:

- Qwen2.5 local Q2_K: SHA-256 `9ee36184e616dfc76df4f5dd66f908dbde6979524ae36e6cefb67f532f798cb8`; manifest selects Q4_K_M (`74a4da8c9fdbcd15bd1f6d01d621410d31c6fc00986f5eb687824e7b93d7a9db`). Existing Q2_K smoke evidence cannot be transferred to Q4_K_M.
- SmolLM2 local Q2_K: SHA-256 `d13c8b95c170f16175bbd4e28ba4bd8d23647039ac63be20d1eb0a26598fbdd3`, revision `10ac3ac5c618d6cdbe76c2845747feed4b5d71ac`; manifest selects Q4_K_M at revision `25eef036a8d245a5a188b1b45d4b240a39bc9f24`. Existing Q2_K smoke evidence cannot be transferred to Q4_K_M.

No model files were downloaded or changed. The unresolved SmolLM2 training-data provenance review remains open. Production service, database, roster, and active tournament state were not modified.
