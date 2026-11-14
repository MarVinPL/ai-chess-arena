# Issue #132 — SmolLM2-1.7B-Instruct GGUF candidate review

Date: 2026-11-14  
Decision: **review_required / not admitted / not technically evaluated**

## Pinned artifact metadata

- Base model: `HuggingFaceTB/SmolLM2-1.7B-Instruct`
- Base-model repository revision: `31b70e2e869a7173562077fd711b654946d38674`
- Quantized repository: `QuantFactory/SmolLM2-1.7B-Instruct-GGUF`
- Current repository revision checked: `6702e34d1eebb7d21caaa1658c98e9d481e69732`
- Selected file: `SmolLM2-1.7B-Instruct.Q4_K_M.gguf`
- File-introducing commit: `d6802b513a12fa4da4e5ab8f3d8c2c65dba1091d`
- Size: `1,055,609,536` bytes
- LFS SHA-256/OID: `bc8986d129483f44768b5fcc2bd2f148d6af000282cdb9ec7a92122226fa7921`
- Xet hash: `bcd82788bee56c24e07ee66317f7538cc5696ad00d1c2a50aaea9898e7e8c91f`
- Declared license tag: `apache-2.0`

The Hugging Face API tree at the pinned repository revision reports the exact file path, byte size, LFS OID and Xet hash above. The selected file is available at the immutable repository revision, not a floating `main` URL.

Sources:
- https://huggingface.co/QuantFactory/SmolLM2-1.7B-Instruct-GGUF/tree/6702e34d1eebb7d21caaa1658c98e9d481e69732
- https://huggingface.co/QuantFactory/SmolLM2-1.7B-Instruct-GGUF/commit/d6802b513a12fa4da4e5ab8f3d8c2c65dba1091d
- https://huggingface.co/HuggingFaceTB/SmolLM2-1.7B-Instruct/tree/31b70e2e869a7173562077fd711b654946d38674
- https://www.apache.org/licenses/LICENSE-2.0

## License and provenance review

The derived GGUF repository and upstream instruction model declare Apache-2.0. This is documentary evidence of the declared model/artifact license, not a legal opinion and not proof that all training-data rights have been cleared.

The upstream SmolLM2 model card identifies FineWeb-Edu, DCLM, The Stack and additional mathematics/coding datasets as training sources. The instruction model also describes supervised fine-tuning using public and curated datasets, preference optimization using UltraFeedback, and function-calling data such as Argilla Synth-APIGen.

Open questions:
- Exact immutable dataset revisions and subsets used for this specific 1.7B checkpoint are not fully identified by the model card.
- The exact DCLM variant/subset needs confirmation.
- FineWeb-Edu has its own ODC-By terms and Common Crawl terms may also apply.
- The Stack requires attention to the original license and attribution of each source file and the project's version/hosting terms.
- The model card does not fully enumerate every curated mathematics/coding dataset and its applicable terms.
- UltraFeedback and Synth-APIGen need to be matched to the precise versions used and their applicable terms.

Therefore `license_review_status=review_required` and `license_verified=false`. Do not infer permission to download, redistribute, or use the weights from the Apache-2.0 tag alone.

## Technical and resource status

No weights were downloaded and no inference was run for this candidate. The selected GGUF is approximately 1.06 GB on disk; that is not a RAM requirement. The VPS previously reported 3.7 GiB total RAM and about 1.7 GiB available, so a runtime test must not be assumed safe from file size alone. Before any test, recheck free memory, disk, service state and active tournaments, and obtain separate explicit authorization to download/run the artifact.

The existing Llamafile runtime has not been independently validated against this 1.7B artifact. `technical_readiness=not_evaluated`.

## Arena restrictions

- Candidate catalog entry only; `arena_model_id=null`.
- `admission_status=not_admitted`, `admission_scope=[]`.
- No entry added to `manifests/huggingface-models.json` or `manifests/model-allowlist.json`.
- No production database, tournament roster, results, or running service changed.
- No download, installation, inference, or tournament admission authorized by this issue.
