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
- `ARENA_LOG_DIR`: absolute path for API JSONL logs. Default: `<application-root>/logs`.
- `ARENA_BACKGROUND_JOBS`: `1` (default) starts the optional Hugging Face
  planners/queue workers; `0` disables those background loops for isolated tests.

## Persistent systemd configuration

The tracked template is [`runtime-process.env.example`](../runtime-process.env.example).
For the systemd service, copy it to `/etc/ai-chess-arena/runtime-process.env`
with root ownership and mode `0640`. The unit loads it as an optional
`EnvironmentFile`; missing file preserves the existing defaults. The service
unit change is part of this PR only and is not installed on the VPS by this
change.

## Isolated integration example

Use a private, disposable directory and a separate process (never point it at
the production database):

```sh
install -d -m 0700 /tmp/arena-api-integration
ARENA_DATABASE_PATH=/tmp/arena-api-integration/arena.sqlite3 \
ARENA_LOG_DIR=/tmp/arena-api-integration/logs \
ARENA_HTTP_HOST=127.0.0.1 ARENA_HTTP_PORT=0 \
ARENA_BACKGROUND_JOBS=0 ARENA_RUNTIME_DEPLOYMENT=disabled \
python3 server.py
```

The API creates the selected database parent with mode `0700` and the log
directory with mode `0700`. Disabled/staging profiles do not construct the
production-only runtime dispatcher; they can serve the ordinary API while
remaining execution-inert.

This does not enable runtime execution. `ARENA_RUNTIME_DEPLOYMENT` remains
`disabled` by default, and the separate production acknowledgement, backend,
paths, broker and supervisor requirements remain mandatory. Do not point the
isolated process at the production database. The settings are not a substitute
for the B1 end-to-end test with a real supervisor service and the API-owned
broker; that release gate remains open.
