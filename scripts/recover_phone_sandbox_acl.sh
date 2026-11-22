#!/usr/bin/env bash
# Issue #281: resume a failed stage deployment whose exclusive DB already exists.
# The previous rollout safely rolled back before installing a Caddy site.
set -euo pipefail
[[ $# -eq 1 && $1 =~ ^[0-9a-f]{40}$ ]] || {
  echo "Usage: recover_phone_sandbox_acl.sh <reviewed-merged-main-SHA>" >&2; exit 2;
}
[[ $(id -u) -eq 0 ]] || { echo 'Root required' >&2; exit 2; }
sha=$1
repo=/home/debian/ai-chess-arena
link=/home/debian/ai-chess-arena-phone-e2e-current
db=/var/lib/ai-chess-arena-phone-e2e/arena.sqlite3
unit=ai-chess-arena-phone-e2e.service
unit_file=/etc/systemd/system/$unit
caddy_file=/etc/caddy/sites/ai-chess-phone-e2e.caddy
python=/home/debian/ai-chess-arena/.venv/bin/python
domain=arena-worker-test.148-113-182-137.sslip.io

runuser -u debian -- git -C "$repo" fetch --no-tags origin main
[[ $(runuser -u debian -- git -C "$repo" rev-parse origin/main) == "$sha" ]] ||
  { echo 'Main changed, refusing unreviewed replay' >&2; exit 2; }
[[ -L $link && -f $db && ! -e $unit_file && ! -e $caddy_file ]] ||
  { echo 'Not the expected safely rolled-back staging state' >&2; exit 2; }
getent passwd arena-phone-test >/dev/null || { echo 'Staging identity missing' >&2; exit 2; }
[[ $(stat -c %U "$db") == arena-phone-test ]] ||
  { echo 'Staging DB owner mismatch' >&2; exit 2; }

# Existing test source must be an already-reviewed ancestor of CURRENT main.
staged=$(runuser -u debian -- git -C "$link" rev-parse HEAD)
runuser -u debian -- git -C "$repo" merge-base --is-ancestor "$staged" "$sha" ||
  { echo 'Unreviewed staging source' >&2; exit 2; }

systemctl is-active --quiet ai-chess-arena.service ||
  { echo 'Production service unhealthy' >&2; exit 2; }
[[ $(ss -ltn '( sport = :8191 )' | wc -l) -eq 1 ]] ||
  { echo 'Staging port occupied' >&2; exit 2; }

# Dedicated staging UID can only traverse home; its token-hash DB remains
# unreadable to anyone except the test UID and root.
setfacl -m u:arena-phone-test:--x /home/debian
runuser -u arena-phone-test -g arena-phone-test -G debian -- \
  sh -c 'cd /home/debian/ai-chess-arena-phone-e2e-current && test -r remote_worker_phone_sandbox.py'
runuser -u arena-phone-test -g arena-phone-test -G debian -- \
  "$python" "$link/remote_worker_phone_sandbox.py" status --db "$db" > /dev/null

installed_unit=0
installed_caddy=0
rollback() {
  result=$?
  if [[ $result != 0 ]]; then
    echo "STAGE_RESUME_ROLLBACK=$result" >&2
    if [[ $installed_caddy == 1 ]]; then
      rm -f "$caddy_file"
      caddy validate --config /etc/caddy/Caddyfile >/dev/null 2>&1 &&
        systemctl reload caddy || true
    fi
    if [[ $installed_unit == 1 ]]; then
      systemctl stop "$unit" || true
      rm -f "$unit_file"
      systemctl daemon-reload || true
    fi
  fi
  exit "$result"
}
trap rollback EXIT

install -m 0644 -o root -g root \
  "$link/systemd/ai-chess-arena-phone-e2e.service" "$unit_file"
installed_unit=1
systemctl daemon-reload
systemctl start "$unit"
for n in 1 2 3 4 5 6 7 8; do
  if curl -fsS --max-time 3 http://127.0.0.1:8191/health |
    grep -Fq '"sandbox": true'; then break; fi
  sleep 1
done
curl -fsS --max-time 4 http://127.0.0.1:8191/health |
  grep -Fq '"sandbox": true' ||
  { echo 'Sandbox listener unhealthy' >&2; exit 1; }

install -m 0644 -o root -g root "$link/caddy/ai-chess-phone-e2e.caddy" "$caddy_file"
installed_caddy=1
caddy validate --config /etc/caddy/Caddyfile
systemctl reload caddy
ok=0
for n in 1 2 3 4 5 6 7 8 9 10 11 12; do
  if curl -fsS --max-time 10 "https://$domain/health" |
    grep -Fq '"sandbox": true'; then ok=1; break; fi
  sleep 2
done
[[ $ok == 1 ]] || { echo 'Public HTTPS failed' >&2; exit 1; }
echo "SANDBOX_RECOVERED https://$domain"
echo "PRODUCTION_DB_NEVER_WRITTEN; production service never restarted"
trap - EXIT
