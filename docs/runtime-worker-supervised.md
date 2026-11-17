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
