# Issue #295 / PR #296 — reconstruction audit, 2026-11-28

## Scope and evidence
- Base: 7684f426e39c4a0959e779816c7c50bbc74cbd77.
- Original PR head: 32996ac740f4a539a717d29bd4c2123cea4ed600; local backup archive/pr296-before-reconstruction-20261128.
- Implementation head before audit: 7386df39ac71ec04df09c99bf58b8d686e7613ed, six commits, 35 files.
- Branch reconstruct/issue-295-native-model-cache; isolated worktree /home/debian/ai-chess-arena-reconstruct296.
- Full Python regression at implementation head: 1459 passed, 1 skipped, 138 subtests passed; 74.70 seconds, exit 0.
- Additional HTTP authentication regression: 8 passed in 4.10 seconds (including new browser-session rejection test).
- ARM64 local APK: version 0.1.3, versionCode 1003, compile/target SDK 37, min SDK 24. APK signature v2 verified with isolated test certificate, zipalign passed, six AArch64 ELF libraries verified, extractNativeLibs true. SHA256 18e33d0556ff18b7a476e78d0aa1eb3839d66f9aedb96ddfacb3ac298230f011.
- Evidence directory /home/debian/pr296-validation; stage7-local-test-arm64.apk, stage7-apk-validation.log, stage8-full-regression.log, stage8-auth-tests.log.
- No production signature, device E2E or GitHub Actions for reconstructed HEAD.

## Disposition of 17 omitted original paths
| Path | Disposition |
|---|---|
| docs/issue-295-main-sync-dry-run-2026-11-23.md | Historical evidence retained in archive; superseded by this audit. |
| remote_worker_gui/src-tauri/Cargo.lock | Preserve main dependency lock. |
| remote_worker_gui/src-tauri/Cargo.toml | Preserve 0.1.3; no unused direct Tokio dependency added. |
| remote_worker_gui/src-tauri/src/android_quic_bridge.rs | Duplicate bridge; consolidated in android_model_quic.rs. |
| remote_worker_gui/src-tauri/src/android_update_download.rs | Cosmetic unused import deferred to #402. |
| remote_worker_update_pause.py | Keep main v4 STOP contract; private resume offer/transport not reconstructed, defer #307–310. |
| server.py | Keep main routing, integrate model-source in remote_worker_phone_api.py. |
| tests/test_android_apksigner_ci.py | Preserve current signing contract. |
| tests/test_hf_candidate_downloads.py | Preserve current model policy; archived staging isolation change not ported; review separately. |
| tests/test_remote_worker_metrics.py | Preserve current auth/metrics contract. |
| tests/test_worker_android_board_view.py | Preserve current board contract. |
| tests/test_worker_android_inference_delivery.py | Preserve current v2/v3/v4 delivery tests. |
| tests/test_worker_android_signing_ci.py | Preserve current signing isolation. |
| tests/test_worker_android_skip_targets_ci.py | Preserve current desktop build restrictions. |
| tests/test_worker_phone_move_provider.py | Preserve current move provider; archived additional cases require review against current protocol. |
| tests/test_worker_resume_status_after_update.py | Preserve main resume status; deferred native flow #307–310. |
| tests/test_worker_single_auth_route_pr296.py | Adapted core auth regression into current HTTP integration suite. |
| tests/test_worker_update_resume_offer.py | Deferred with private resume transport #307–310. |

Note: inventory contains 18 rows; prior count of 17 was an off-by-one. All listed paths are accounted for; classification is not proof of behavioral equivalence.

## Scope not complete
Native runtime, downloader, maintenance and STOP helpers are foundations, not an end-to-end executor. Actual v4 preparation/dispatch, bounded legal move generation, model transfer progress, resource enforcement, filesystem TOCTOU/crash recovery, durable STOP outbox and resume require integration and tests. Do not enable native inference, automatic model discovery, or tournament dispatch. #291, #325–327, #307–311, #314, #391/#399/#400 remain open. No original certificate device update test. Issue #295 must remain open. New backend metadata endpoint and maintenance schema require review before deployment. Static ABI validation is not physical JNI/QUIC runtime proof. Existing main functionality preserved by additive reconstruction and passing regression; not a claim of exhaustive equivalence.

## Synchronization plan
Conditional GO for updating the existing draft only; NO-GO for merge, release, production deployment or activation.
1. Confirm remote main and original PR head again. Stop if either changed; re-audit.
2. Preserve original local archive and obtain explicit authorization for remote history rewrite; recommend remote backup ref before rewrite, separately authorized.
3. Only with explicit approval, update existing PR head using exact expected old OID force-with-lease, never unconditional force push; no duplicate PR. Preserve Draft.
4. Verify remote HEAD and new CI Android-only, update PR report and Issue checklist with limitations. Do not close Issue.
5. Address remaining lifecycle/security dependencies and physical Android E2E before merge decision. Separate production approval required.

No push, merge, production changes or inference activation performed.
