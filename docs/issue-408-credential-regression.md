# CredentialVault regression (#408)

Baseline: main 3356496, migration NO-GO (#407). No production code, credential identities, dependencies, Kotlin/JNI or IPC changes. The preserved #209 prototype is not imported.

Added Rust tests for competing first registration and backend mutation failure; the synthetic backend models the unavailable state after an uncertain commit. Added Python source-contract tests for stable legacy identifiers, absent migration dependencies and registration/deletion journal guards. Existing journal initialization, reconciliation and installer tests remain unchanged.

Verification commands: `python -m pytest -q tests`; `cargo test --offline --locked --lib` in `remote_worker_gui/src-tauri`. The existing Worker selective builds workflow performs Android ARM64 PR compilation, with desktop builds still paused. Target-specific compilation does not establish physical persistence. Dedicated Python credential suite: 10 passed. Verified test-code commit `693a752`: full Python regression **1462 passed, 1 skipped, 138 subtests passed** (89.93 s); full Rust library regression **84 passed, 0 failed**. Both new Rust tests passed. [Python CI](https://github.com/MarVinPL/ai-chess-arena/actions/runs/46163031085) and [Android ARM64 APK CI](https://github.com/MarVinPL/ai-chess-arena/actions/runs/46163031153) passed for that commit. Linux and Windows skipped as intended. No physical device persistence test was performed.

Physical restart, upgrade and recovery tests remain #311; backup exclusions #435 and RustSec remediation #434 are separate. Do not close #209 before its remaining acceptance gates are resolved. No production token access or deployment.
