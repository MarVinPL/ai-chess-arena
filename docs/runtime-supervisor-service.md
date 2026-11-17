# Runtime supervisor daemon — Issue #157

`runtime_supervisor_service.py` is the root-owned daemon entry point. Importing
it has no host side effects. `build_service()` validates the complete explicit
production configuration, verifies pre-provisioned roots and the root-owned
pinned worker, constructs the read-only job authority, durable lease store,
exact-unit systemd backend and SO_PEERCRED-gated AF_UNIX listener.

## Required configuration

Start from `runtime-supervisor.env.example`. The API and supervisor must point
to the exact same canonical Arena SQLite database. The artifact allowlist must
contain exact `repo|revision|filename` keys; wildcards and empty values are
rejected. The worker must be root-owned, executable, not group/world writable,
and match the independently reviewed SHA-256 pin.

The daemon refuses startup unless it runs as root, production acknowledgement
and execution opt-in are explicit, all identities exist and are distinct,
and the following roots already exist with exact owner/group/mode:

- artifact handoff: supervisor:supervisor `0711`;
- runtime output: supervisor:supervisor `0755`;
- artifact quarantine: API:API-group `0711`;
- supervisor socket parent: supervisor:API-group `0750`.

It creates no accounts, groups, directories, ACLs, sockets, systemd units,
cgroups, firewall rules or worker files. The listener is created only by the
explicit `main()` entry point after validation.

## Deployment blocker — database write isolation

The current durable lease store intentionally writes `runtime_unit_leases` in
the same SQLite database as `runtime_test_jobs`, so lease persistence can
atomically verify the API's running claim and cancellation flag. The API also
writes that database. A production systemd unit must grant the privileged
supervisor only the database and the minimum SQLite journal/WAL sidecar access,
not the whole application checkout. That least-privilege file-access contract
has not yet been implemented or validated. Therefore this change deliberately
does **not** add or install an enableable `.service` unit. A broad writable
`/home/debian/ai-chess-arena` allowance would be an unacceptable shortcut.

Before production: design and test the database access boundary, validate the
actual systemd sandbox and mount behavior in a disposable VM, provision roots
and identities under separate authorization, install the pinned worker bundle,
then run independent security review. No production action is performed by
this module.
