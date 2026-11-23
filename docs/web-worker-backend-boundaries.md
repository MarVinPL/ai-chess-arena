# Backend boundaries — Issue #314

This is the first stage of separating the AI Chess Arena web panel from its
Android/Remote Worker API. It changes module ownership, **not** deployment
topology: one Python HTTP service and one authoritative SQLite state remain.

## Request flow

```text
HTTP request -> server.Handler  (composition root; existing port and paths)
                |
                +-- POST /api/remote-workers/* --> worker_backend
                |   |-- authenticate: WorkerTokenStore machine token
                |   +-- opt-in routes: remote_worker_phone_api
                |            |-- DurableWorkerJobs / inference delivery
                |            +-- authoritative shared SQLite checkpoint/lease
                |
                +-- Web panel --> web_panel_auth (login, logout, origin/rate limits)
                    |-- arena_auth browser sessions
                    |-- web_panel_backend (exact static asset allowlist)
                    +-- admin/model/tournament handlers (incremental extraction)
                               |
                               +-- shared chess / model / checkpoint modules
```

## Security invariants

1. Worker endpoints must be dispatched **before** browser-session checks,
   using machine tokens for every recognized Worker operation. A valid web
   session cookie never authenticates the Worker.
2. The Worker endpoint allowlist is exact. Unknown routes must not expose
   privileged actions. The enable flag remains off unless explicitly enabled.
3. Panel assets remain behind browser-session authentication and use a
   fixed URL-to-file mapping; no user-controlled filesystem paths.
4. The Worker HTTP path does not commit chess moves or create/finalize
   authoritative checkpoints. Coordinator CAS and SQLite remain shared,
   without a duplicate database, allowlist or alternate result authority.
5. No new service, port, deployment, migration, APK or credential handling.

## Compatibility and migration

- Public endpoint paths and successful/failed response formats remain
  unchanged by this stage.
- `server.Handler.send_panel_asset` is kept as an adapter for existing
  callers/tests, with the implementation in `web_panel_backend`.
- `server.Handler.do_POST` delegates Worker authentication and the
  phone routes to `worker_backend.dispatch_worker_post` first; it then
  delegates browser login/logout to `web_panel_auth.dispatch_panel_auth_post`.
- Existing panel business API handlers remain inside `server.py` for now.
  Further extraction must keep auth and CSRF checks in front of every panel
  action, and requires its own tests within Issue #314 before closing it.
- Unfinished Draft PR #197, #292 and #296 contain overlapping Worker
  changes. Reconcile their changes deliberately; do not overwrite them with
  this branch or infer runtime readiness from this refactor alone.

## Verification / rollback

Run `python -m pytest -q tests/test_issue_314_backend_boundaries.py
tests/test_panel_static_assets.py tests/test_remote_worker_phone_sandbox.py`,
then the full Python CI suite. Confirm worker token rejection and panel
cookie-only denial, and check that no route has changed semantics.

Revert the #314 PR on staging to restore the previous single-file dispatch
if regression is found. Production deployment requires separate approval.
