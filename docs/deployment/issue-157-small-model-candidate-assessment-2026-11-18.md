# Small-model candidate assessment — 2026-11-18

## Scope and safety

Metadata-only review of small GGUF candidates and a deterministic calculation using the existing `runtime_resource_policy.py`. No model weights were downloaded, copied, admitted, loaded or executed. No inference, TEST_ONLY grant, roster change, runtime allowlist change or production change occurred.

## Host and policy

At assessment time, `/proc/meminfo` reported `MemAvailable` **1.624 GiB** (`MemTotal` 3.737 GiB). The current policy reserves 1.5 GiB for the model cgroup and 1.5 GiB for the host, plus 1.5× the artifact size. Therefore the minimum required host availability is `artifact_size × 1.5 + 3 GiB`. Even a zero-byte artifact cannot pass with less than 3 GiB available.

| Candidate / quantization | GGUF size | Required available RAM | Shortfall now | Result |
|---|---:|---:|---:|---|
| SmolLM2 360M Q2_K | 208.54 MiB | 3.305 GiB | 1.682 GiB | BLOCKED |
| SmolLM2 360M Q4_K_M | 258.06 MiB | 3.378 GiB | 1.754 GiB | BLOCKED |
| Qwen2.5 0.5B Q2_K | 395.95 MiB | 3.580 GiB | 1.956 GiB | BLOCKED |
| Qwen2.5 0.5B Q4_K_M | 468.64 MiB | 3.686 GiB | 2.063 GiB | BLOCKED |

Exact file sizes and LFS SHA-256 values were read from the Hugging Face revision metadata API (`?blobs=true`), not from downloaded weights.

## Candidate findings

### SmolLM2 360M Instruct — not recommended for the current Arena

- Repo: `QuantFactory/SmolLM2-360M-Instruct-GGUF`
- Pinned revision: `10ac3ac5c618d6cdbe76c2845747feed4b5d71ac` (API revision matched).
- Smallest listed Q2_K file: `SmolLM2-360M-Instruct.Q2_K.gguf`, 218,673,792 bytes; LFS SHA-256 `d13c8b95c170f16175bbd4e28ba4bd8d23647039ac63be20d1eb0a26598fbdd3`.
- The quantized mirror declares Apache-2.0 but has no separate `LICENSE`/`COPYING` file in its file index. The upstream SmolLM2 card states Apache 2.0, but this is not a completed source-chain or dataset-rights review.
- The upstream card says SmolLM2 models primarily understand and generate English. That makes this candidate a poor fit for a Polish-first Arena workflow.
- The mirror metadata has no `text-generation` pipeline tag (it is tagged `conversational`).
- RAM preflight remains blocked by 1.682 GiB for the smallest Q2_K file.

Disposition: retain only as a historical metadata candidate; do not select it for runtime review or download.

### Qwen2.5 0.5B Instruct — better multilingual metadata candidate, but still blocked

- Repo: `Qwen/Qwen2.5-0.5B-Instruct-GGUF`
- Pinned revision: `9217f5db79a29953eb74d5343926648285ec7e67` (API revision matched).
- The official repo includes `LICENSE`, declares Apache-2.0, and identifies `text-generation` as its pipeline.
- Its model card describes multilingual support across 29+ languages. Polish is not explicitly named in the retrieved language list, so Polish performance must not be assumed; a task-specific test would be required later.
- Smallest listed Q2_K file: `qwen2.5-0.5b-instruct-q2_k.gguf`, 415,182,688 bytes; LFS SHA-256 `9ee36184e616dfc76df4f5dd66f908dbde6979524ae36e6cefb67f532f798cb8`.
- RAM preflight remains blocked by 1.956 GiB.

Disposition: preferred candidate for future metadata/license and Polish-language evaluation, but **not** selected, downloaded, or runtime-approved.

### LFM2.5 350M — smaller, but license/language trade-offs

- Repo: `LiquidAI/LFM2.5-350M-GGUF`, pinned revision `657e078c94084481950a2d555a941481f715536b`.
- The card declares custom `lfm1.0` (Hugging Face tag `other`) and includes LICENSE files. This needs separate license compatibility review.
- Declared languages are English, Arabic, Chinese, French, German, Japanese, Korean and Spanish; Polish is not among them.

Disposition: not selected.

### Apodex 1.1-mini — misleading size label

The currently existing download allowlist contains one Apodex IQ1_M artifact. Its card describes a 35.95B-parameter MoE model and an IQ1_M file of 8.82 GB. Despite `1.1-mini` in the name, it is not a small-RAM candidate. This assessment did not modify that pre-existing download allowlist entry.

## Decision

**No model currently qualifies for the existing VPS RAM budget under the unchanged conservative policy.** SmolLM2 is the smallest listed option but is English-centric and fails preflight. Qwen2.5 0.5B is a more promising multilingual candidate, but also fails preflight. The policy's fixed 3 GiB reserves alone exceed current available RAM.

Next safe step: retain Qwen2.5 0.5B as a metadata-only preferred candidate and investigate host capacity or obtain an explicit decision on a revised resource policy. Do not weaken resource reserves, stop unrelated services, download weights, or run inference as part of this issue without separate authorization.

## Sources

- SmolLM2 GGUF: https://huggingface.co/QuantFactory/SmolLM2-360M-Instruct-GGUF
- Upstream SmolLM2 Instruct card: https://huggingface.co/HuggingFaceTB/SmolLM2-360M-Instruct
- Qwen2.5 0.5B GGUF: https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF
- Qwen2.5 documentation: https://qwen.readthedocs.io/en/v2.5/
- LFM2.5 350M GGUF: https://huggingface.co/LiquidAI/LFM2.5-350M-GGUF
- Apodex 1.1-mini GGUF: https://huggingface.co/abenzerps/Apodex-1.1-mini-GGUF
