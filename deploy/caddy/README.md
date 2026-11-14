# AI Chess Arena Caddy module

This directory contains only the AI Chess Arena site route. It is intentionally
not imported or activated by this repository change.

## Audited current behavior

The current shared host configuration contains one site block for
`arena.148-113-182-137.sslip.io` with:

- HTTP Basic Authentication for the existing `arena` account;
- reverse proxy to `127.0.0.1:8090`;
- no route changes requested for other projects or services.

The VPS Caddy binary was observed as version 2.6.2. This module therefore uses
the `basicauth` directive spelling supported by that installed version.

## Secret handling

The module references `{$ARENA_BASIC_AUTH_HASH}`; it does not contain a
credential or password hash. Supply the existing bcrypt hash through a
root-owned environment file readable by the `caddy` service account. Do not
copy the hash into Git, this README, shell history, or command output.

Example file path: `/etc/caddy/arena-auth.env`

The file should contain exactly an environment assignment:

```sh
ARENA_BASIC_AUTH_HASH='<existing-bcrypt-hash>'
```

Recommended ownership and permissions are `root:caddy` and `0640`. Add
`EnvironmentFile=/etc/caddy/arena-auth.env` to a systemd drop-in for
`caddy.service`. The service account must be able to read the file. Do not
change the live unit or create the file as part of this repository task.

## Controlled migration (operator action, not performed here)

1. Back up the complete current Caddy configuration and the existing systemd
   unit/drop-ins using the host's approved backup procedure.
2. Install this file as `/etc/caddy/sites/ai-chess-arena.caddy`.
3. Provision the environment file and systemd drop-in as described above,
   preserving the existing credential exactly.
4. Validate the environment and the complete candidate Caddyfile before
   changing the active configuration.
5. Remove only the Arena site block from the shared Caddyfile and enable
   `import /etc/caddy/sites/*.caddy` in its documented import section.
   Preserve every other service block and global option.
6. Run `caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile`
   against the complete candidate configuration.
7. Review the diff and confirm the Arena hostname, BasicAuth behavior, and
   loopback upstream are unchanged. Only then may an operator separately
   authorize reload/activation.
8. Rollback: restore the backed-up shared Caddyfile and systemd drop-in,
   validate the restored full configuration, and reload only with separate
   authorization.

## Validation

The repository test checks that this module contains only the Arena hostname,
the existing loopback upstream, BasicAuth, and an environment-variable
reference—not a literal bcrypt hash or credential.

The VPS preflight validated the currently active complete Caddyfile
successfully. No live file, systemd unit, environment file, route, or service
was changed by this issue.
