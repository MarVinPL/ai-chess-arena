# Light-R1-14B-Q4_0 license-chain review — 2026-11-18

## Scope and disposition

This is a metadata/source-document review only. No GGUF or other model weights were downloaded, copied to quarantine, hashed locally, loaded, or executed.

**Internal result: PERMISSIVE LICENSE EVIDENCE FOUND; FINAL ADMISSION NOT APPROVED.** The official Light-R1 project publishes an Apache-2.0 license and states that all released project materials follow it. The official model and official GGUF repository also declare Apache-2.0. The candidate mirror's exact GGUF LFS SHA-256 and size match the official `qihoo360` GGUF repository. The declared base chain points through Light-R1-14B-DS to DeepSeek-R1-Distill-Qwen-14B, whose published model license is MIT and whose card identifies Qwen2.5 as the original base (Apache-2.0).

This is strong evidence of a permissive model-weight license chain, but it is not a legal opinion or proof of every training-data right. The model card describes training data as collected from public sources without a complete per-dataset license/attribution inventory. The GGUF repository itself has no separate LICENSE/NOTICE file. Therefore the record deliberately remains `review_required` and `verified_permissive=false` pending release-owner acceptance of this evidence and the residual training-data limitation. Do not infer permission for unrelated datasets, trademarks, or third-party materials.

## Exact candidate identity

- Candidate repo: `360ZhiNao/Light-R1-14B-DS-GGUF`
- Pinned revision: `0d4531e58894e0019d4a9a6aa775eb32098213cb`
- File: `Light-R1-14B-Q4_0.gguf`
- Size: `8,517,725,312` bytes
- Upstream LFS SHA-256: `d9fb85f0d6582ef0ac061dcf54e1236aff2d4583a83c0aebff281c5fb3764afa`
- Candidate repository API confirms `license:apache-2.0`, `base_model_relation:quantized`, and base `qihoo360/Light-R1-14B-DS`.

## Evidence chain

1. **Publisher project license:** `https://github.com/Qihoo360/Light-R1/blob/40b5965eb554469e9f311f8d74942141a3d6a1d4/LICENSE` is Apache License 2.0. The GitHub API reported LICENSE blob `b09cd7856d58590578ee1a4f3ad45d1310a97f87`. The project README's License & Acknowledgements section states: “All released materials of this project follow the open-source license Apache 2.0.” Source: `https://github.com/Qihoo360/Light-R1`.
2. **Official Light-R1 model:** `https://huggingface.co/qihoo360/Light-R1-14B-DS` declares Apache-2.0 in both repository tags and card metadata; API revision observed: `a69e32b88daa855ae068ca8601f3ec4085e96086`. Its model card identifies `deepseek-ai/DeepSeek-R1-Distill-Qwen-14B` as its base and describes Light-R1 post-training.
3. **Official GGUF publication:** `https://huggingface.co/qihoo360/Light-R1-14B-DS-GGUF` declares Apache-2.0 and identifies `qihoo360/Light-R1-14B-DS` as its base; API revision observed: `803c4846d22abb993af547feadc1c184cf5bd945`.
4. **Mirror-to-official byte identity by upstream metadata:** both the pinned candidate repo/revision and official `qihoo360` GGUF API return the same Q4_0 file size (`8,517,725,312`) and LFS SHA-256 (`d9fb85f0d6582ef0ac061dcf54e1236aff2d4583a83c0aebff281c5fb3764afa`). This is upstream metadata equality only; no local file was obtained or hashed.
5. **DeepSeek base license:** `https://huggingface.co/deepseek-ai/DeepSeek-R1-Distill-Qwen-14B` declares MIT; its pinned API revision observed was `1df8507178afcc1bef68cd8c393f61a886323761`, and its repository contains a `LICENSE` file. DeepSeek's model card states that the Qwen-14B distill is derived from Qwen2.5 and describes the model weights/code as MIT-licensed.
6. **Original Qwen base:** `https://huggingface.co/Qwen/Qwen2.5-14B-Instruct` declares Apache-2.0 and contains a LICENSE file (API revision observed: `cf98f3b3bbb457ad9e2bb7baf9a0125b6b88caa8`).
7. **Training-data caveat:** Light-R1's card describes training on public data and links a technical report, but this review did not establish a complete dataset-by-dataset rights/attribution inventory. This remains a limitation, not an assumption of infringement or permission.

## Review outcome and gates

- `publisher_project_license`: `apache-2.0` — source file inspected.
- `official_model_license_metadata`: `apache-2.0` — confirmed.
- `official_gguf_license_metadata`: `apache-2.0` — confirmed.
- `candidate_mirror_matches_official_gguf_lfs_metadata`: `true` — size and upstream SHA match.
- `base_model_license_evidence`: `MIT` for DeepSeek distill; `apache-2.0` for Qwen2.5 base — source metadata/license files found.
- `training_data_rights_inventory`: `not_verified`.
- `candidate_gguf_license_file`: `absent`.
- `license_status`: `review_required`.
- `verified_permissive`: `false`.
- `technical_readiness`: `not_assessed_by_this_review`.
- `quarantine_artifact_present`: `false`.
- `weights_downloaded`: `false`.
- `local_hash_recomputed`: `false`.
- `runtime_test_only_grant_active`: `false`.
- `inference_authorized`: `false`.
- `tournament_admission`: `false`.

To close the license sub-gate, the release owner must explicitly accept the documented license evidence and residual dataset limitation, or keep the candidate blocked. Even an accepted license review does not itself authorize downloading weights, creating a quarantine artifact, granting TEST_ONLY, executing inference, or admitting the model to the tournament; those remain separate gates.

## Release-owner acceptance — 2026-11-18

**Decision: ACCEPTED WITH RECORDED LIMITATION.** The release owner explicitly accepted the documented Apache-2.0/MIT model-weight license evidence and acknowledged the stated limitation that a complete dataset-by-dataset training-data rights and attribution inventory was not verified. The license sub-gate is recorded as **PASS_WITH_RECORDED_LIMITATION**.

This is an owner risk-acceptance decision for the reviewed model-weight license evidence, not a statement that every training dataset has been independently cleared. Accordingly `verified_permissive` remains `false`; the dataset-rights limitation and absence of a separate GGUF LICENSE/NOTICE remain visible. The acceptance does not admit the model artifact, authorize downloading weights, create a quarantine artifact, recompute the local hash, activate TEST_ONLY, execute inference, alter the tournament roster, or authorize production deployment. Those are separate gates and remain closed. Machine-readable owner decision: `issue-157-model-license-owner-acceptance-2026-11-18.json`.


## Operational correction — artifact later found in quarantine (2026-11-18)

The initial scope statement above was incomplete: a matching GGUF file was already present in the quarantine directory, with filesystem timestamps from 2026-11-16. Its full local SHA-256 was subsequently recomputed and matched the pinned upstream LFS digest. The original transfer provenance remains unknown because no matching download job/event record exists. The artifact was reconciled into the verified-quarantine registry. This does not change the license conclusion: owner acceptance remains subject to the dataset-rights caveat and `verified_permissive=false`; no TEST_ONLY grant or inference authorization exists. See `issue-157-model-artifact-quarantine-2026-11-18.md`.
