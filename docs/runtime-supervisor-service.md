# Runtime supervisor daemon — Issue #157

`runtime_supervisor_service.py` is the root-owned daemon entry point. Importing
it has no host side effects. `build_service()` validates the complete explicit
production configuration, verifies pre-provisioned roots and the root-owned
pinned worker and immutable root-owned supervisor zipapp, constructs the
read-only job authority, API lease-broker client, exact-unit systemd backend
and SO_PEERCRED-gated AF_UNIX listener. Direct execution from the writable
application checkout is refused.

## Required configuration

Build the supervisor package from the reviewed source tree with:

```sh
python3 tools/build_runtime_supervisor_bundle.py --output /tmp/runtime_supervisor.pyz
sha256sum /tmp/runtime_supervisor.pyz
```

The builder includes only the explicit supervisor dependency closure and emits
a deterministic zipapp. Installation is a separate privileged operation: place
it at `/usr/local/lib/ai-chess-arena-runtime/runtime_supervisor.pyz`, make it
`root:root` and non-group/world-writable, then set the exact reviewed SHA-256
in the root-owned environment file. Launch it only with `/usr/bin/python3 -I -S`
(isolated mode with site initialization disabled); the bootstrap refuses
non-isolated execution, verifies
ownership, mode, canonical path and digest before importing service modules,
and disables bytecode writes. `build_service()` rechecks the same pin. This
repository change does not install the bundle.

Start from `runtime-supervisor.env.example`. The API and supervisor must point
to the exact same canonical Arena SQLite database for the supervisor's
read-only authorization checks. Lease writes are performed by the API-owned
broker at `/run/ai-chess-arena-api/lease-broker.sock`; the supervisor does not
open the application database read/write. The API process runs as `debian:debian`
and creates the broker socket as `debian:debian` mode `0660`. The supervisor runs
as `root:debian` with no `CAP_DAC_OVERRIDE`, so the API-group write bit is
required for the root peer to connect; the broker still authenticates the peer
UID as exactly `0` through `SO_PEERCRED`. The API service must provision its own
`/run/ai-chess-arena-api` runtime directory before starting the broker.
The artifact allowlist must
contain exact `repo|revision|filename` keys; wildcards and empty values are
rejected. The worker must be root-owned, executable, not group/world writable,
and match the independently reviewed SHA-256 pin.

The daemon refuses startup unless it runs as root, production acknowledgement
and execution opt-in are explicit, all identities exist and are distinct,
and the following roots already exist with exact owner/group/mode:

- artifact handoff: `root:debian` `0711`;
- runtime output: `root:debian` `0755`;
- artifact quarantine: API:API-group `0711`;
- supervisor socket parent: `root:API-group` `0750`.

The supervisor unit runs as `root:debian`, so its effective UID/GID are
`0:debian`. The service validator derives these values from the process
identity and rejects any other ownership. The dedicated API runtime directory
is provisioned by the API systemd unit as `debian:debian` mode `0750`; the
broker socket itself is `debian:debian` mode `0660`, allowing the root
supervisor (group `debian`) to connect. The broker still authenticates the
supervisor's peer UID as exactly `0`.

It creates no accounts, groups, directories, ACLs, sockets, systemd units,
cgroups, firewall rules or worker files. The listener is created only by the
explicit `main()` entry point after validation.

## Deployment blocker — read-only database and broker validation

The API-owned lease broker now performs transactional lease writes and
cancellation checks. The privileged supervisor uses the broker for lease
persistence/state/recovery and retains a read-only connection for authorization.
The repository includes a proposed `ai-chess-arena-runtime-supervisor.service`
and an API dependency drop-in example. A disposable KVM guest validated the
supervisor's notify/readiness, restart/SIGTERM lifecycle, real transient worker
start/stop and several isolation boundaries. Basic SQLite WAL read-only behavior
was also verified. The remaining production API/broker lifecycle and shutdown
ordering, full SQLite sidecar matrix, production worker/artifact mount, release
provenance and independent security review are still open. A broad writable
`/home/debian/ai-chess-arena` allowance remains prohibited.

Before production: validate that the actual API process creates a `0660`
`debian:debian` broker socket and that the root supervisor connects using only
its bounded capabilities; complete API/supervisor/broker startup and shutdown
ordering; finish the SQLite and worker matrices; then provision roots and
identities under the reviewed release procedure and complete independent
security review. No production action is performed by this module.


## API shutdown and active-job cancellation

The API process handles SIGTERM/SIGINT as a graceful shutdown request rather
than relying on Python's default process termination. It stops accepting HTTP
requests, marks only the currently dispatched runtime job as cancellation
requested while the API-owned SQLite broker is still available, and waits for
the dispatcher to finish its runner cleanup before closing the broker. The
executor observes the durable cancellation flag; the supervisor rechecks the
same authorization before starting a unit and the runner requests an exact-unit
stop for a unit that has already started.

The API unit's TimeoutStopSec must cover the maximum supervisor start IPC
request (1800 seconds) plus the bounded exact-unit stop window (120 seconds)
and a small shutdown margin. This intentionally makes service stop potentially
long when an artifact handoff/start operation is slow; it avoids killing the
API and closing its lease broker while a start transition is unresolved.
