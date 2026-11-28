# Worker Android v0.1.0 — original-signature test prerelease (#233)

The **only** purpose of this one-shot prerelease is to test whether Android
accepts an APK as an update to the already installed Worker **without
uninstalling it or clearing saved app data**. This is separate from the full
remote-worker protocol in PR #193 and the stacked feature PR #216.

## Evidence and immutable input

| Property | Original APK on the VPS | CI APK before re-signing |
| --- | --- | --- |
| Package | `pl.marvin.aichessarena.worker` | same |
| Version | `0.1.0`, versionCode `1000` | same |
| CPU ABI | `arm64-v8a` | same |
| Public signing cert SHA-256 | `3f0449ec3a2399ec669cce2dcbd6aa7262671345dfb983c33e2cadf4d1b0da12` | `524335e647bacf4275b138171388670660f0dd9c9632177807f1d9571dc3811a` |

The new source APK was built and uploaded by [run #45018325702](https://github.com/MarVinPL/ai-chess-arena/actions/runs/45018325702) from commit
`2c8e6660202e8cab032e7533c2b1c9ed507d6a47` (stacked Worker branch, **not** main).
Its SHA-256 is pinned to
`fb1a9df1909fbb9f91bf3b881bc798fef98185848b1a04534ffed27bf96d16fe`.
The historical original APK from the VPS and matching keystore were verified
independently using `apksigner` and `keytool`. The private key is not
recoverable from the APK.

## Trusted, free procedure

1. Merge the reviewed Issue #233 PR into `main` only after its tests pass.
   This workflow change does **not** rebuild Worker source.
2. Manually run **Worker Android original-key test release (no compilation)**
   from `main` in GitHub Actions. It refuses other branches.
3. `sign` has only read permissions. It pins the exact old CI run, downloads
   its ZIP artifact, verifies checksum, package, version and architecture,
   then restores the signing key from existing GitHub Actions Secrets to a
   private runner-temporary file. The private key is never uploaded.
4. `apksigner` re-signs the ZIP-aligned APK and verifies its public certificate.
   All checks fail closed; SHA256SUMS and PROVENANCE.txt are prepared.
5. A separate `publish` job, with **no signing secrets** and narrowly scoped
   `contents: write`, creates the GitHub **prerelease** tag
   `worker-v0.1.0-test.1`. The tag targets the actual source APK commit,
   not `main`; never falsely claim the APK was compiled from `main`.
   Existing test releases are never overwritten.

### On-device acceptance

- Leave the old Worker installed and its data intact.
- Download the APK **only** from the test prerelease's assets and choose
  Android's ordinary installation/update confirmation. Do not uninstall first.
- Confirm whether the package installer offers **Update** and completes
  successfully, and whether app settings/token survive.
- If it fails, record the exact Android installer error. CI verification of
  package name and signing identity alone is **not** on-device proof.
- The APK still has version `0.1.0` / `1000`, so it does **not** provide a
  higher-version release or an in-app update notification; it tests signature
  continuity with the existing installation.

**Security:** This is **debug-key compatibility only**, not an acceptable
production signing setup. The previous debug keystore used Android's conventional
default debug-key passwords and does not establish strong publisher identity.
No private keystore is committed, logged, copied into assets, or given to
untrusted PR jobs. Distribution is manual. No new paid services are used.
The in-app updater remains disabled until authenticated idle-state and signature
checks exist. Linux and Windows builds remain paused.

**Naming convention:** published prerelease APK filenames use `pre`, not
`test` (for example `ai-chess-arena-worker-v0.1.0-pre1-arm64.apk`).
The already established immutable historical release tag remains unchanged;
this naming adjustment does not change the Android package, versionCode,
or signing certificate.
