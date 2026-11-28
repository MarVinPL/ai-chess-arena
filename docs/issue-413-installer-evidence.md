# #413 — native Android installer evidence

Internal read-only evidence, no Tauri IPC or journal mutation. The collector
uses Android PackageManager's installed package, versionCode and single current
signer, requiring the existing pinned release certificate. Package install and
update timestamps classify FirstInstallHint, Updated or Unknown. A first-install
hint is not proof of empty storage: data restore and missing history are possible.

PackageInstaller's own session inventory includes the session ID, and the
private callback preferences are read with one getAll snapshot. Partial,
incorrectly typed and unknown status records fail closed. JNI exceptions on
the evidence read paths are cleared and returned as generic errors. An absent
session or absent callback is never terminal evidence.

InstalledCandidate and FailedCandidate require the journaled session ID,
terminal callback, no visible own session, and respectively exact target or
previous version with the trusted signer. They are advisory observations only.
The old receiver does not provide a cryptographically fresh callback or proof
of successful durable preference commit; session-ID reuse/stale callbacks
cannot be ruled out by this reader. Do not treat either candidate as authority
to clear a hold or journal. This task does not change the installation protocol.

The collector repeats native observations and rejects observable changes or
reads exceeding three seconds. This is not a transaction across Android OS
services and cannot exclude ABA races or installations by other apps.
#414/#416 must combine fresh evidence with fenced coordinator authorization,
durable journal identity and explicit reconciliation; #415 handles first
registration. No migration or deployment. Physical verification remains #311.

Tests: standalone Rust policy tests with synthetic identities, history, session
and callback values, plus Python integration contract tests. Android ARM64 CI
must compile JNI paths before merge; physical JNI behaviour is not established
by host tests.

Review: the existing observation-only wrapper retains its original contract;
the detailed observer additionally returns the session ID. The source-level
regression now asserts the new tuple shape and the compatibility wrapper.
