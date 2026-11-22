# Issue #275: real Android Worker UCI → Arena checkpoint, isolated HTTPS

The sandbox is a separate 127.0.0.1:8191 Python API that accepts only
machine authentication and the five Worker routes from issue #272. It runs
without the main server bootstrap, model planners, admin UI or tournament
runner. Caddy terminates TLS at the free sslip.io address:

https://arena-worker-test.148-113-182-137.sslip.io

The production domain and service continue to operate without changes.
Never enable ARENA_PHONE_INFERENCE_ENABLED in production.

## Security / data flow

An explicit offline administrator bootstrap operation reads the existing
production SQLite credential table READ-ONLY and copies only the token_digest
(SHA-256 of one high-entropy bearer secret), expiry, and one selected worker
identifier into a separate SQLite file under private directory mode 0700
and file mode 0600. No new token is issued, and no plaintext credential leaves
the phone. This digest remains credential-sensitive: never publish or back it
up, and never expose the staging database through HTTP.

The standalone test service does not open the production database at all.
It starts only a loopback listener, and refuses paths unrelated to the
Worker protocol. Admin operations are local CLI commands, never HTTP routes.

## Operator workflow (after merge, CI and review)

1. Run deploy_phone_sandbox.sh --preflight <merged-main-SHA>.
2. Run deploy_phone_sandbox.sh --deploy <merged-main-SHA>.
3. Verify HTTPS /health and that a deliberately invalid token is rejected.
4. On the phone keep Termux llama-server running on 127.0.0.1:8088 with
   an alias equal to the actual model SHA-256. In Android Worker temporarily
   change only the Arena HTTPS address to the sandbox URL. Keep the existing
   worker ID android_marvin_01 and saved token, click Sprawdź połączenie HTTPS,
   then Start lokalnie.
5. Admin reads sandbox status until the model becomes ready, then runs the
   trusted local-only arm command in remote_worker_phone_sandbox.py.
   It issues one v2 assignment with all legal moves, the initial FEN,
   model SHA and a maximum 600-second lease.
6. Android pulls the job, checks STOP, invokes Termux llama.cpp and returns
   a valid UCI. Admin reads status for the received result and calls finalize
   to atomically commit the next FEN and release the sandbox Worker lease.
7. The app should show the move awaiting Arena, then no new assignment after
   finalization. Revert the HTTPS address to:
   https://arena.148-113-182-137.sslip.io
   Verify the production connection once more.
8. After inspecting the stored UCI/FEN, admin runs
   cleanup_phone_sandbox.sh --after-finalize to stop staging, remove its
   Caddy site and purge the temporary credential digest and test database.
   The script refuses cleanup if the job is unfinished.

## Evidence criteria

- Production database tournament status and token row unchanged.
- HTTPS test host returns a sandbox /health and 401 on invalid bearer.
- Correct token is only accepted for the selected Worker ID.
- Model SHA matches local GGUF alias.
- ready -> trusted arm -> assigned -> received_not_applied
  -> trusted finalize -> idle_confirmed.
- Checkpoint has exactly one legal UCI and the corresponding FEN.
- Zero assigned/pending results after commit.
- No billable third-party services: existing VPS, Caddy and free TLS.

The test is isolated but is not a full tournament. A later PR must wire
automatic model selection, full runner and resilience into real tournaments.
