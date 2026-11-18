# Fixed supervised worker entrypoint

runtime_worker_supervised.py is the source for the root-owned executable
installed as /usr/local/lib/ai-chess-arena-runtime/runtime_worker. The
systemd backend accepts exactly:

    /usr/local/lib/ai-chess-arena-runtime/runtime_worker --job JOB_ID

The worker accepts no model path, executable, port, environment override or
command string from the API. It derives the model path from the per-job
output directory, which contains the exact read-only BindReadOnlyPaths mount.
The host-side /var/lib/arena-handoff tree is inaccessible inside the unit.

Before launching, it checks:
- API UID/GID supplied by the trusted systemd unit Environment properties;
- output-root and per-job directory ownership/modes;
- exact model file is regular, runtime-owned, mode 0400 and within the artifact
  size cap;
- bundle root and worker entrypoint are root-owned/non-writable;
- llamafile is root-owned, executable, non-writable and matches the pinned
  SHA-256.

It starts only the pinned llamafile binary with fixed loopback-only options:
127.0.0.1:18080, one thread, 2048 context, parallel=1, temperature=0, no UI,
no warmup. The worker starts the AF_UNIX bridge in the same private network
namespace and keeps both processes inside the unit cgroup. Resource limits
are additionally applied with RLIMIT_CPU, RLIMIT_FSIZE, RLIMIT_NOFILE and
RLIMIT_CORE; systemd remains the authoritative memory/CPU/PID/wall-clock
boundary.

The bridge leaves control.sock behind. The root supervisor removes it only
after the exact unit cgroup is empty. The worker never opens the Arena DB,
quarantine root, SSH material, API secrets or any other job artifact.

This is source code only. The executable, modules and pinned llamafile have
not been installed under /usr/local; no systemd unit or model process was
started. The bundle's effective systemd mount order, no-egress behavior and
actual service identities still require disposable integration tests.

## Deterministic executable bundle

`tools/build_runtime_worker_bundle.py` assembles the worker entrypoint and its
three first-party modules into a deterministic Python zipapp named
`runtime_worker`. The archive has a fixed `/usr/bin/python3` shebang, sorted
entries, fixed ZIP timestamps and no third-party dependencies. Build it with:

    python3 tools/build_runtime_worker_bundle.py --output /tmp/runtime_worker

The executable is pinned as a single file by `ARENA_RUNTIME_WORKER_SHA256`;
the privileged supervisor verifies its SHA-256, root ownership, and
non-writable permissions before accepting a start request. The worker checks
that it was invoked from the exact canonical bundle path.

The worker's output-root check follows the supervisor's actual ownership
contract: `/run/arena-runtime` is owned by `root:debian`, mode `0755` (not
`root:root`). It resolves the expected supervisor group by name and fails
closed if that group is unavailable or the directory does not match.

A disposable build/import/argument-parser test passed on the VPS on
2026-11-18. Candidate artifact SHA-256:
`d7d7ab4be3637bb9c618e1e6d1ec67cbfcd7d847b791b31d045b7d7486d6dc1d`.
This is a build reproducibility/test hash, **not production approval**. The
artifact was created under `/var/tmp` only; it was not installed in
`/usr/local`, not configured in the supervisor environment, and not executed
with a model. It must be rebuilt from the exact reviewed commit and independently
reviewed before installation.
