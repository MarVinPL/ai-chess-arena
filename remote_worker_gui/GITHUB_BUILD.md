# GitHub Actions — Android Worker (#198)

Workflow: .github/workflows/android-worker-apk.yml.

Runs on GitHub-hosted Ubuntu for PRs touching Worker, main changes and manual dispatch. Produces an ARM64 **debug** APK and SHA-256 as downloadable Actions artifacts; it does not consume VPS RAM.

**Debug APK is not an automatic update release.** The Android debug signing certificate created on the runner is not guaranteed to match the APK installed from VPS. Never publish it as an in-place update or ask users to uninstall their app. A stable, protected release signing key, verified signature continuity, versionCode increment, controlled GitHub Release publication and on-device updater validation are prerequisites for automatic updates. Do not put private signing keys in Git.
