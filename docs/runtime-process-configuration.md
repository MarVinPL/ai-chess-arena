# API process configuration

The Arena API keeps its existing production defaults while allowing an
isolated process to use a disposable SQLite database and an independently
allocated loopback HTTP port. These settings are read once at process import:

- `ARENA_DATABASE_PATH`: absolute path to the SQLite database. Default:
  `<application-root>/arena.sqlite3`. The server, `league` module and
  Hugging Face download metadata store are all pointed at this same path.
- `ARENA_HTTP_HOST`: loopback IP address only. Default: `127.0.0.1`.
  `localhost` is normalized to `127.0.0.1`; non-loopback binds are rejected.
- `ARENA_HTTP_PORT`: integer from `0` through `65535`. Default: `8090`.
  Port `0` asks the OS for an available ephemeral port and is intended for
  isolated integration tests. The actual bound address is logged after bind.

Example for a local integration process (use a private, disposable directory):

```sh
install -d -m 0700 /tmp/arena-api-integration
ARENA_DATABASE_PATH=/tmp/arena-api-integration/arena.sqlite3 \
ARENA_HTTP_HOST=127.0.0.1 ARENA_HTTP_PORT=0 \
python3 server.py
```

This does not enable runtime execution. `ARENA_RUNTIME_DEPLOYMENT` remains
`disabled` by default, and the separate production acknowledgement, backend,
paths, broker and supervisor requirements remain mandatory. Do not point the
isolated process at the production database. The settings are not a substitute
for the B1 end-to-end test with a real supervisor service and the API-owned
broker; that release gate remains open.
