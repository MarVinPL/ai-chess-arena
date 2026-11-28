# Android Worker credential recovery (#404)

The existing alias, preferences name, ciphertext format and worker identity remain unchanged.
A missing entry is distinct from an unreadable entry. A read never creates a key.
An existing unreadable envelope cannot be overwritten or removed by normal Android token operations.

If the store is unavailable, stop new work and updates, retain app data and ciphertext,
and check device unlock, package/signing continuity and outstanding update reservations.
Retry after resolving a transient failure. Do not clear application data, delete the alias,
or automatically generate a replacement key. If the key is irrecoverable, coordinate
authenticated recovery and token replacement with the Arena administrator before any
destructive action; this change deliberately provides no automatic reset.
A new token cannot decrypt an old envelope. Pending update reconciliation remains #405/#406.
The old boolean IPC remains compatible; the new status IPC returns only saved/missing/unavailable.
Local synthetic tests do not establish physical persistence or Android JNI runtime behavior;
ARM64 CI and device tests (#311/#408) are required.

A failed SharedPreferences commit blocks further credential access in the current process:
the in-memory value may differ from durable storage. Restart and recheck the store; this
is not proof of coordinator reconciliation or of the previous commit's outcome.
