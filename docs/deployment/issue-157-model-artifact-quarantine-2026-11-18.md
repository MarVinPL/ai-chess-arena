# Light-R1 artifact quarantine reconciliation — 2026-11-18

## Result

**Existing artifact found, locally hash-verified, and reconciled into the verified-quarantine registry.** The owner's current approval did not cause a second download: the exact file was already present on the VPS before the approval.

- Repository: `360ZhiNao/Light-R1-14B-DS-GGUF`
- Revision: `0d4531e58894e0019d4a9a6aa775eb32098213cb`
- File: `Light-R1-14B-Q4_0.gguf`
- Size: `8,517,725,312` bytes
- Expected and recomputed SHA-256: `d9fb85f0d6582ef0ac061dcf54e1236aff2d4583a83c0aebff281c5fb3764afa`
- Quarantine path: `/var/lib/ai-chess-arena/hf-candidate-downloads/e81293fe0425410eadb2a72fc4ee4bd3/d7330e8c69__Light-R1-14B-Q4_0.gguf.quarantine`
- File mode/owner: `0600`, UID `1000`; regular file, not a symlink; GGUF magic confirmed
- Filesystem mtime: `2026-11-16T19:31:59.999409Z`; ctime: `2026-11-16T19:32:00.083409Z`

The full local SHA-256 was recomputed on the VPS and matches the pinned upstream LFS SHA-256. The exact artifact was then reconciled into `/var/lib/ai-chess-arena/arena.sqlite3` table `hf_download_verified_artifacts` at `2026-11-18T21:37:32Z`.

## Provenance limitation

No matching `hf_download_jobs` or `hf_download_events` record exists for this file. Its path matches the downloader's repository-tag naming convention and its content matches the pinned upstream digest, but the original transfer actor, exact transfer time, and transfer method cannot be established from the available records. Filesystem timestamps are not treated as proof of transfer provenance. This discrepancy corrects earlier Issue #157 notes that said no quarantine artifact existed; those notes reflected the missing registry entry and were incomplete.

## Runtime preflight

The metadata/hardware preflight was refreshed after reconciliation. Result: `skipped_blocked`, not runtime-ready. Blockers: (1) no manual candidate review record for this pinned revision in the runtime review subsystem; (2) estimated RAM requirement approximately `14.90 GiB` versus `1.63 GiB` available. Quarantine volume had approximately `8.45 GiB` free. Runtime remains disabled and inactive.

## Permission boundary

The owner accepted the license evidence with the training-dataset caveat, but `verified_permissive` remains false. No TEST_ONLY grant is active. No runtime handoff, inference, tournament admission, or production deployment is authorized. No model was loaded or executed.

Machine-readable evidence: `issue-157-model-artifact-quarantine-2026-11-18.json`.


## Owner-requested removal — 2026-11-18

The owner subsequently requested that Light-R1 be removed from both the candidate list and quarantine. The exact Light-R1 file was deleted. The same request was extended to Spark-X2.5 4B; all three matching quarantined GGUF variants were deleted as well:

| Model file | Size (bytes) | SHA-256 (recorded before deletion) |
|---|---:|---|
| `Light-R1-14B-Q4_0.gguf` | 8,517,725,312 | `d9fb85f0d6582ef0ac061dcf54e1236aff2d4583a83c0aebff281c5fb3764afa` |
| `Spark-X2.5-4B-Q4_0.gguf` | 2,405,581,632 | `33873ba76d69a72231ab0790d879cd252d53ee0fe648c3b44b04dec69fea25ec` |
| `Spark-X2.5-4B-Q4_K_M.gguf` | 2,600,223,552 | `7934660bfc5b9bf04be0a0ac6179a1d16e1d4331b448857c86b8b2801b3ef72c` |
| `Spark-X2.5-4B-Q5_K_M.gguf` | 2,977,892,352 | `d585f155289ed7b73f2f644e475445fcc40f1aa5131ca807eade1c726895535a` |

Before deletion, each exact target was confirmed as a regular non-symlink file with the recorded size; the Spark hashes were recomputed during the removal inspection and the Light-R1 digest was the previously recomputed upstream-matching local hash. All four paths are now absent. Matching rows were removed from `hf_download_verified_artifacts`; four durable records were written to `hf_artifact_removal_events`. No model was loaded or executed. The active candidate manifest excludes Light-R1 and all Spark-X2.5 4B variants; historical identifiers and digests remain here for auditability.

This removal does not close B3. A smaller model must be found and pass the actual host RAM preflight; the current host has approximately 1.63 GiB available.
