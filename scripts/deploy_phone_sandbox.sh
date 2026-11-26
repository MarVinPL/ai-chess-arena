#!/usr/bin/env bash
# Issue #275: separate Arena Worker HTTPS sandbox, no production tournament writes.
# Only a merged, exact SHA in origin/main is deployable.
set -euo pipefail

repo=/home/debian/ai-chess-arena
unit=ai-chess-arena-phone-e2e.service
unit_file=/etc/systemd/system/$unit
caddy_file=/etc/caddy/sites/ai-chess-phone-e2e.caddy
source_db=/var/lib/ai-chess-arena/arena.sqlite3
stage_root=/var/lib/ai-chess-arena-phone-e2e
stage_db=$stage_root/arena.sqlite3
current_link=/home/debian/ai-chess-arena-phone-e2e-current
python=/home/debian/ai-chess-arena/.venv/bin/python
domain=arena-worker-test.148-113-182-137.sslip.io
worker=android_marvin_01

[[ $# -eq 2 ]] || { echo "Usage: $0 --preflight|--deploy <merged-main-40-char-SHA>" >&2; exit 2; }
[[ "$1" == --preflight || "$1" == --deploy ]] ||
  { echo 'Unknown operation' >&2; exit 2; }
mode=$1
sha=$2
[[ $sha =~ ^[0-9a-f]{40}$ ]] ||
  { echo 'Expected exact merged SHA' >&2; exit 2; }
[[ $(id -u) -eq 0 ]] || { echo 'Requires root' >&2; exit 2; }
[[ -f $source_db && -x $python ]] || { echo 'Existing production DB/runtime missing' >&2; exit 2; }
[[ ! -e $unit_file && ! -e $caddy_file && ! -e $current_link && ! -e $stage_db ]] ||
  { echo 'Sandbox already exists: refuse to overwrite' >&2; exit 2; }

runuser -u debian -- git -C "$repo" fetch --no-tags origin main
head=$(runuser -u debian -- git -C "$repo" rev-parse origin/main)
[[ $head == "$sha" ]] || { echo 'Unreviewed main commit: refusing' >&2; exit 2; }
for file in remote_worker_phone_sandbox.py caddy/ai-chess-phone-e2e.caddy systemd/ai-chess-arena-phone-e2e.service; do
  runuser -u debian -- git -C "$repo" cat-file -e "$sha:$file" ||
    { echo "Missing reviewed file: $file" >&2; exit 2; }
done
systemctl is-active --quiet ai-chess-arena.service || { echo 'Production service unhealthy' >&2; exit 2; }
[[ $(ss -ltn '( sport = :8191 )' | wc -l) -eq 1 ]] ||
  { echo 'Port 8191 already listening' >&2; exit 2; }
getent passwd arena-phone-test >/dev/null &&
  { echo 'Dedicated staging account already exists' >&2; exit 2; } || true

"$python" - "$source_db" <<'PY'
import sqlite3,sys,time
c=sqlite3.connect("file:"+sys.argv[1]+"?mode=ro",uri=True)
status=c.execute("SELECT status,count(*) FROM tournaments GROUP BY status").fetchall()
print("Production tournament summary:",status)
if any(name not in ("planned","completed") for name,n in status if n):
    raise SystemExit("Active tournament: not safe for this rollout window")
row=c.execute(
    "SELECT expires_at,revoked_at FROM worker_tokens WHERE worker_id=?",
    ("android_marvin_01",)
).fetchone()
if not row or row[1] is not None or row[0] <= time.time():
    raise SystemExit("Worker token unavailable for hash-only staging")
print("SOURCE_READ_ONLY=PASS")
PY
if [[ $mode == --preflight ]]; then
  echo 'PHONE_SANDBOX_PREFLIGHT_PASS'
  exit 0
fi

short=$(printf '%s' "$sha" | cut -c1-8)
work=/home/debian/ai-chess-arena-phone-e2e-$short
[[ ! -e $work ]] || { echo 'Target worktree already exists' >&2; exit 2; }

# Creating a separate worktree from main ensures the production ExecStart is untouched.
runuser -u debian -- git -C "$repo" worktree add --detach "$work" "$sha"
ln -s "$work" "$current_link"
install -d -m 0700 -o debian -g debian "$stage_root"
runuser -u debian -- "$python" "$current_link/remote_worker_phone_sandbox.py" \
  bootstrap --db "$stage_db" --source-db "$source_db" --worker-id "$worker"
# The long-lived sandbox process has a distinct UID from production.
# Group debian grants traversal of /home/debian and access to the venv,
# never ownership access to the 0700 production DB directory.
useradd --system --no-create-home --home-dir /nonexistent \
  --shell /usr/sbin/nologin arena-phone-test
chown -R arena-phone-test:arena-phone-test "$stage_root"
# The home directory has an ACL overriding the group traversal bit.
# Grant only this service UID the ability to TRAVERSE, not list /home/debian.
setfacl -m u:arena-phone-test:--x /home/debian
runuser -u arena-phone-test -g arena-phone-test -G debian -- \
  sh -c 'cd /home/debian/ai-chess-arena-phone-e2e-current && test -r remote_worker_phone_sandbox.py'

installed_unit=0
installed_caddy=0
rollback() {
  rc=$?
  if [[ $rc != 0 ]]; then
    echo "SANDBOX_ROLLBACK error=$rc" >&2
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
    # Keep the private database for forensic review; never expose the digest.
  fi
  exit "$rc"
}
trap rollback EXIT

install -m 0644 -o root -g root "$work/systemd/ai-chess-arena-phone-e2e.service" "$unit_file"
installed_unit=1
systemctl daemon-reload
systemctl start "$unit"

for n in 1 2 3 4 5 6 7 8 9 10; do
  curl -fsS --max-time 3 http://127.0.0.1:8191/health |
    grep -Fq '"sandbox": true' && break
  sleep 1
done
curl -fsS --max-time 5 http://127.0.0.1:8191/health |
  grep -Fq '"sandbox": true' ||
  { echo 'Sandbox loopback unhealthy' >&2; exit 1; }

install -m 0644 -o root -g root "$work/caddy/ai-chess-phone-e2e.caddy" "$caddy_file"
installed_caddy=1
caddy validate --config /etc/caddy/Caddyfile
systemctl reload caddy

ok=0
for n in 1 2 3 4 5 6 7 8 9 10 11 12; do
  if curl -fsS --max-time 10 "https://$domain/health" |
    grep -Fq '"sandbox": true'; then
    ok=1
    break
  fi
  sleep 2
done
[[ $ok == 1 ]] || { echo 'Public HTTPS sandbox not ready' >&2; exit 1; }
echo "PHONE_SANDBOX_HTTPS_READY https://$domain"
echo 'PRODUCTION_SERVICE_AND_DB_UNCHANGED'
trap - EXIT
