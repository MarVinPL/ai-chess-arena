# Durable update journals (#405)

Android uses two independent AndroidKeyStore AES-256-GCM aliases derived from the existing
credential alias, with slot-bound authenticated data. Ciphertext is stored in private
worker_update_journals_v1 preferences. Existing machine-token identity and desktop keyring
accounts remain unchanged. No migration plugin or new webview command is enabled.

Non-secret E/P/U markers live under Context.noBackupFilesDir in credential-pending-hold
and credential-pending-install, directories mode 0700 and marker files mode 0600.
Each mutation writes U before touching ciphertext; marker replacement uses exclusive staging,
file sync, rename and directory sync. Insert requires absence; delete requires exact expected
record. Verify readback before publishing P or E. Missing, malformed, contradictory or
partially staged markers block the operation. Failed mutations retain uncertainty rather than
blindly undoing a possibly durable change. Recovery must not delete markers or reset app data.

The synchronous change gate serializes token changes and reservation creation, and journal
mutations are serialized. Lock order: change gate, token vault, journal gate; no lock held
over network await. The existing authenticated release/installer protocol is unchanged.

IMPORTANT: uninitialized markers fail closed, including on fresh installations. This is
intentionally not a deployable standalone feature: #406 must supply independently verified
identity, coordinator and installer evidence and wire the initialization/reconciliation path.
The prototype initialization helper is not called by production code. Do not publish or
deploy until that integration and physical restart/update checks (#311/#408) are complete.
A legacy mock store cannot establish absence of an outstanding remote hold.

Verification: standalone Rust synthetic failure/restart tests and Python source-contract
tests. ARM64 build and real device persistence still require verification.
