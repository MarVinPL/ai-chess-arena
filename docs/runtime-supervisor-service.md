# Runtime supervisor daemon — Issue #157

`runtime_supervisor_service.py` is the root-owned daemon entry point. Importing
it has no host side effects. `build_service()` validates the complete explicit
production configuration, verifies pre-provisioned roots and the root-owned
pinned worker, constructs the read-only job authority, API lease-broker client,
exact-unit systemd backend and SO_PEERCRED-gated AF_UNIX listener.

## Required configuration

Start from `runtime-supervisor.env.example`. The API and supervisor must point
to the exact same canonical Arena SQLite database for the supervisor's
read-only authorization checks. Lease writes are performed by the API-owned
broker at `/run/ai-chess-arena-api/lease-broker.sock`; the supervisor does not
open the application database read/write. The API service must provision its
own `/run/ai-chess-arena-api` runtime directory before starting the broker.
The artifact allowlist must
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

## Deployment blocker — read-only database and broker validation

The API-owned lease broker now performs transactional lease writes and
cancellation checks. The privileged supervisor uses the broker for lease
persistence/state/recovery and retains a read-only connection for authorization.
The broker protocol and integration tests do not substitute for the required
disposable-VM validation of actual SQLite WAL permissions, systemd sandboxing,
socket ownership and restart ordering. This change deliberately does **not**
add or install an enableable `.service` unit. A broad writable
`/home/debian/ai-chess-arena` allowance remains prohibited.

Before production: validate the implemented database access boundary and the
actual systemd sandbox/mount behavior in a disposable VM, provision roots and
identities under separate authorization, install the pinned worker bundle,
then run independent security review. No production action is performed by
this module.
