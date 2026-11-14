# Operator authentication with Passkeys (Issue #136)

## Decision

The operator identity for model-permission mutations is application-level WebAuthn/Passkey authentication. Caddy Basic Auth remains an outer access layer only; it is not an operator identity and must never be translated into an actor.

This document records the approved implementation direction. It does not enable authentication, permission mutations, model execution, or production changes.

## Trust boundaries

- Each human operator has a distinct immutable operator ID and an explicit role.
- The application verifies WebAuthn registration and authentication ceremonies with a maintained, open-source server library.
- The server derives the actor exclusively from a server-side session created after successful WebAuthn assertion verification.
- The browser never supplies an actor or role. Request JSON, query parameters, Authorization, X-Authenticated-User, X-Forwarded-User, source IP, and the shared Unix service account are not identity evidence.
- The session cookie is random, opaque, Secure, HttpOnly, SameSite=Strict, path-scoped, and backed by a server-side hash/expiry record. Mutating requests additionally validate the exact Origin and a session-bound CSRF token.
- WebAuthn challenges are random, single-use, short-lived, bound to the ceremony and operator, and consumed atomically.
- Authentication requires user verification. RP ID and origin are explicit deployment configuration; absent or inconsistent configuration fails closed. No localhost, IP address, or guessed production hostname fallback is permitted.
- Credential public keys, credential IDs, signature counters, operator roles, sessions, and audit events are stored in the Arena database. Private keys remain with the authenticator.

## First-owner enrollment

There is no public self-registration endpoint and no default owner account.

1. An operator with local VPS shell access runs a dedicated provisioning command under the Arena service's approved administrative procedure.
2. The command creates a random, one-use enrollment token, stores only its cryptographic hash and a short expiry, and prints the enrollment URL once to the local terminal.
3. The owner completes a WebAuthn registration ceremony from the configured HTTPS origin with user verification required.
4. The token is consumed atomically only after successful credential verification. It cannot create another owner or be reused.
5. The command and enrollment flow do not expose a route for unauthenticated role changes or additional owner creation.

The exact CLI privilege boundary, secure token delivery, recovery procedure, and owner credential revocation must be implemented and reviewed before production use. No token is to be embedded in source, committed to Git, placed in a URL log, or sent through a third-party service.

## Additional operators and roles

- Additional operator enrollment is initiated by an authenticated owner and is bound to a named operator record and a one-use invitation.
- The initial role set is deliberately small: owner and operator.
- Only an owner may approve/reject/revoke model permission requests. An operator may submit a request but cannot approve it.
- Every permission decision records the verified operator ID, role snapshot, timestamp, model/revision/artifact digest, scope, reason, and result.
- Role changes and credential revocation invalidate active sessions. Owner removal requires a separately verified owner and must never leave the system without an owner.

## Ceremony and session controls

- Use a maintained WebAuthn server implementation; do not implement CBOR, COSE, attestation, or signature verification manually.
- Require HTTPS, exact configured RP ID/origin, challenge freshness, origin/RP ID hash checks, user presence, and user verification.
- Registration requires a discoverable credential and rejects duplicate credential IDs.
- Authentication looks up the credential server-side and verifies the assertion against its stored public key and counter.
- Challenges are single-use and expire quickly; failed, expired, replayed, cross-origin, or malformed ceremonies fail closed.
- Session identifiers are generated from a cryptographically secure random source; only hashes are persisted. Sessions rotate after authentication and are bounded by idle and absolute expiry.
- Cookie-authenticated state changes require same-origin checks and CSRF protection. Responses containing authentication material use no-store cache headers.
- Rate-limit enrollment, authentication, and recovery attempts. Do not disclose whether an operator ID or credential exists in unauthenticated responses.
- Audit logs must never contain raw enrollment tokens, session IDs, cookies, WebAuthn challenges, or private credential material.

## Deployment configuration

The implementation must require explicit configuration for:
- ARENA_WEBAUTHN_RP_ID: the exact DNS RP ID (not a URL and not an IP literal);
- ARENA_WEBAUTHN_ORIGIN: the exact HTTPS origin including scheme and optional port;
- session signing/pepper material from a protected runtime secret source, not the repository or a world-readable environment file.

The application validates that the origin host is the RP ID or a valid subdomain of it. Missing, malformed, insecure, or inconsistent configuration disables all enrollment and authentication routes. Tests use isolated test-only RP IDs and origins.

## Rollout gates

1. Implement and test the WebAuthn ceremony and database schema in disposable databases.
2. Implement the local one-use owner bootstrap command and review its privilege boundary.
3. Implement session rotation, expiry, CSRF/origin validation, rate limits, and credential revocation.
4. Integrate actor resolution into the four model-permission mutation routes; keep them denied until the first owner is provisioned.
5. Add role checks so only owner can approve/reject/revoke; operator can only submit requests.
6. Add browser tests and negative tests for replay, wrong origin/RP ID, expired challenge, spoofed headers/body actor, CSRF, session fixation, revoked credential, and privilege escalation.
7. Perform a security review before any production configuration or database change.

Until every gate is met, model_permission_auth.authenticated_actor() remains fail-closed and all four mutation routes return HTTP 403. No production enrollment, DB migration, configuration edit, deployment, model download, or model execution is authorized by this design decision.
