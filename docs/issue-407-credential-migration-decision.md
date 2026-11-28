# CredentialVault migration decision (#407)

Decision (2026-11-28): **NO-GO** for migration to tauri-plugin-keyring-store 0.3.2. Retain the current Kotlin/JNI Android Keystore and Rust CredentialVault. Do not activate migration, reset credentials, publish or deploy. This is a documentation-only decision under #209; #408 and physical verification #311 remain outstanding.

## Reviewed inputs

- Main `e544fd8716b31639e104ebb517a499014e73f674`; #406 and #414 completed.
- Preserved prototype `/home/debian/ai-chess-arena-issue209`, HEAD `e7c503de6cf108a19d6ae39cfa8a77ff32651ce2`, seven modified and two untracked files. Preserve unchanged; do not cherry-pick wholesale.
- Cached plugin 0.3.2 (MIT OR Apache-2.0, Rust >=1.88, Tauri >=2.11.2); prototype lockfile uses keyring-core 1.1.0 and vendored android-native-keyring-store 1.0.0. Host Rust 1.100.0 and main Tauri 2.12.7 satisfy declared minima, not proof of integrated compilation. Prototype adds 32 lockfile package entries (not necessarily Android-linked). Default crypto feature disabled but additional crypto dependencies remain.
- Seven standalone synthetic migration tests passed; not physical Keystore or main integration evidence.

## Security rationale

The existing Kotlin/JNI backend already uses Android Keystore, separates absent and unavailable credentials, and fences uncertain commits. The vendored patch checks SharedPreferences commit results, rejects missing keys/config for existing vaults and handles JNI errors. However, initial backend creation writes configuration before creating a key; reads can initialize an absent store, and commit(false) can leave a changed in-memory value. Immediate readback is not proof of durable persistence. Plugin default store registration is global; a future candidate should use explicit backend binding and strict get_password, never the background API which can map a locked store to absence. No new plugin IPC permissions are justified. The local fork creates additional maintenance burden without demonstrated security improvement.

The prototype is copy-only, intentionally blocked, and predates #406. Any future proposal must use current CredentialVault, lock order change gate -> token vault -> journal gate, no synchronous lock across await, authenticated coordinator identity and durable journal reconciliation; it must not orphan pending holds or change Worker ID. Require durable copy/restart verification, explicit conflict handling, rollback retaining both copies, and authenticated token lifecycle tests. No automatic fallback or deletion of the old token. Reopening migration requires owner GO and a separate scoped implementation issue.

## RustSec evidence

`cargo-audit 0.23.0`, database commit `3eebb903ba6c43f60c57dc0e15fa61eef06231d1`, command `cargo-audit audit --no-fetch --db <db> --file <lockfile> --json` against main and preserved prototype lockfiles. Both exit 1 with identical findings:

- RUSTSEC-2026-0594: cookie 0.18.2, private cookie nonce repetition in processes that fork; patched >=0.18.3.
- RUSTSEC-2026-0543: chrono 0.4.45, unmaintained.
- RUSTSEC-2024-0370: proc-macro-error 1.0.4, unmaintained.
- RUSTSEC-2024-0429: glib 0.18.5, unsound iterator implementation.

No incremental advisory was observed; not proof of absence of vulnerabilities. Whole-lockfile scan includes other targets; Android reachability and dependency remediation require separate triage. Do not claim a clean audit or modify dependencies in this decision PR.

## APK backup evidence

GitHub artifact 15269592265, head e544fd8716b31639e104ebb517a499014e73f674, ai-chess-arena-worker-v0.1.3-pre563-arm64.apk: supplied SHA256SUMS verification passed. `aapt dump xmltree <apk> AndroidManifest.xml` (build-tools 36.0.0) shows targetSdk 37, debug build, no explicit allowBackup, fullBackupContent or dataExtractionRules on application. No backup/extraction-named resources were listed by aapt. This verifies the actual debug APK, not every release variant or physical restore behavior.

Current token ciphertext uses worker_machine_credential_v1 preferences; journal ciphertext uses worker_update_journals_v1. Non-secret journal markers use noBackupFilesDir. Default backup eligibility can restore ciphertext without its Keystore key, or separate records from markers; this is an availability/integrity risk, not proof of plaintext exposure. Explicitly exclude credential/journal preferences from cloud backup and device transfer, including applicable legacy rules, and verify merged manifests and physical restore behavior before release. Track separately; do not modify APK configuration in #407. Existing fail-closed behavior and no destructive recovery remain mandatory. Debug manifest also permits cleartext traffic; assess release manifest and native HTTPS policy separately, without inferring actual cleartext token transport.

## Follow-up

#408: regression of current CredentialVault and ARM64, no migration. #311: physical device verification. Separate backlog items for RustSec triage and backup policy (check existing issues first). Update #209 checklist only with verified closure evidence. No production changes.
