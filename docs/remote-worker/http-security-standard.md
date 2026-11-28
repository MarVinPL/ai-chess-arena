# Worker HTTP security baseline — Issue #372 / PR #193

Parent EPIC: #192. This baseline describes the local implementation candidate on 2026-11-26, not production acceptance.

## Required invariants

- Machine credentials are separate from browser sessions. Preserve token expiry, revocation, coordinator ownership and fail-closed behavior. Do not grant installation permission from advisory job status.
- Preserve the stricter security invariant when integrating stacked branches. Changes to a protocol limit require explicit compatibility analysis and regression evidence; do not silently weaken controls to resolve a merge conflict.
- Public Worker POST routes use peer admission, strict bounded JSON parsing, exact protocol fields, token authentication and authenticated identity admission before domain operations. The existing job delivery transaction retains its independent credential and checkpoint checks.
- `remote_worker_http_validation.py`: exact request path, one Content-Length and Content-Type, no Transfer-Encoding or Content-Encoding, UTF-8 JSON object, maximum 2048 bytes, no duplicate keys or non-finite numbers, maximum depth 16, five-second body read inactivity timeout. Invalid framing closes the connection. The internal HTTP adapter uses this parser and retains its existing peer limiter.
- `remote_worker_rate_policy.py`: rolling 60-second windows, bounded peer and identity tables, actual socket peer rather than client-supplied forwarding headers. Public limits per authenticated worker: authenticate/job/update-safety/ready 12 each; job-result 30; job-poll/update-pause-poll/board-view 120 each. Peer aggregate 600, per-route peer limit ten times the identity limit. These are provisional engineering defaults, not measured capacity.

## Integration requirements

Keep machine authentication and the Android API opt-in gate. Do not activate production remote dispatch or weaken STOP/ACK, lease fencing or checkpoint ownership. New machine routes must receive explicit admission policy and validation tests before exposure. Deployment remains separately authorized.

## Evidence and outstanding work

Targeted regression: 72 tests passed in 7.62 seconds with SyntaxWarning treated as error. Includes validator, rate policy, internal HTTP adapter, native job preview, metrics, isolated phone sandbox, atomic checkpoint HTTP and heartbeat tests. Additional public endpoint security integration test execution is not confirmed. Full strict Python regression: 797 tests and 134 subtests passed in 39.68 seconds. GitHub CI for code commit `46cc2433b37c1491afbe64fabc4a98e9e2d9a544`: Python push [45904401277](https://github.com/MarVinPL/ai-chess-arena/actions/runs/45904401277), Python PR [45904407367](https://github.com/MarVinPL/ai-chess-arena/actions/runs/45904407367), Android APK [45904407501](https://github.com/MarVinPL/ai-chess-arena/actions/runs/45904407501) succeeded; desktop and signed APK jobs skipped. Owner reran four existing test modules (phone API main, HTTP ingest, HTTP validation, rate policy): report verified on VPS, 49 passed in 3.04 seconds. These overlap existing regression coverage and are not an independent full public-route integration suite. Remaining acceptance gap: end-to-end coverage of the combined HTTP validator, admission policy and machine authentication across all eight public POST routes. No merge or deployment approval implied. Do not mark #372 accepted until all required checks and review are complete.

Limitations: process-local rate counters reset on restart and do not coordinate across processes; reverse proxy peers can share a budget; load and legitimate polling behavior require measurement. Body timeout is inactivity-only, not an absolute end-to-end deadline or header-read timeout. TLS and proxy/network protections remain necessary. This baseline does not certify complete replay protection or physical Android/desktop E2E.
