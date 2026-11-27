#!/usr/bin/env bash
# Issue #282: recover the exact partial #275 stage, never deploy a new prod app.
set -euo pipefail
repo=/home/debian/ai-chess-arena
unit=ai-chess-arena-phone-e2e.service
unit_file=/etc/systemd/system/$unit
site=/etc/caddy/sites/ai-chess-phone-e2e.caddy
link=/home/debian/ai-chess-arena-phone-e2e-current
db=/var/lib/ai-chess-arena-phone-e2e/arena.sqlite3
source_db=/var/lib/ai-chess-arena/arena.sqlite3
python=/home/debian/ai-chess-arena/.venv/bin/python
domain=arena-worker-test.148-113-182-137.sslip.io

[[ $# -eq 2 && ( "$1" == --preflight || "$1" == --resume ) ]] || {
  echo 'Usage: resume_phone_sandbox_after_chdir_failure.sh --preflight|--resume <merged-main-SHA>' >&2
  exit 2
}
action=$1
sha=$2
[[ $sha =~ ^[0-9a-f]{40}$ && $(id -u) == 0 ]] || {
  echo 'Root and exact merged main SHA required' >&2
  exit 2
}
[[ -L $link && -f $db && -f $source_db && ! -e $unit_file && ! -e $site ]] || {
  echo 'Staging is not in recognized partial-deployment state' >&2
  exit 2
}
[[ $(stat -c %a /var/lib/ai-chess-arena-phone-e2e) == 700 &&
   $(stat -c %a "$db") == 600 ]] || {
  echo 'Staging filesystem permissions unsafe' >&2
  exit 2
}
[[ $(stat -c %U /var/lib/ai-chess-arena-phone-e2e) == arena-phone-test &&
   $(stat -c %U "$db") == arena-phone-test ]] || {
  echo 'Staging database ownership mismatch' >&2
  exit 2
}
id arena-phone-test >/dev/null
systemctl is-active --quiet ai-chess-arena.service || {
  echo 'Production service unhealthy' >&2; exit 2;
}
if systemctl is-active --quiet "$unit"; then
  echo 'Staging service already running; refuse repair' >&2
  exit 2
fi
if ss -lnt | grep -q ':8191'; then
  echo 'Port 8191 already in use' >&2
  exit 2
fi
runuser -u debian -- git -C "$repo" fetch --no-tags origin main
main=$(runuser -u debian -- git -C "$repo" rev-parse origin/main)
[[ $main == "$sha" ]] || { echo 'Main advanced; abort' >&2; exit 2; }
release=$(readlink -f "$link")
case "$release" in
  /home/debian/ai-chess-arena-phone-e2e-*) ;;
  *) echo 'Unknown staging release; abort' >&2; exit 2 ;;
esac
old_sha=$(runuser -u debian -- git -C "$release" rev-parse HEAD)
runuser -u debian -- git -C "$repo" merge-base --is-ancestor "$old_sha" "$sha" ||
  { echo 'Staging binary not in reviewed main history' >&2; exit 2; }

"$python" - "$db" "$source_db" <<'PY'
import hmac,json,sqlite3,sys,time
stage=sqlite3.connect("file:"+sys.argv[1]+"?mode=ro",uri=True)
live=sqlite3.connect("file:"+sys.argv[2]+"?mode=ro",uri=True)
row=stage.execute("SELECT worker_id,token_digest,expires_at,revoked_at FROM worker_tokens").fetchall()
reference=live.execute(
 "SELECT worker_id,token_digest,expires_at,revoked_at FROM worker_tokens WHERE worker_id=?",
 ("android_marvin_01",)
).fetchone()
if len(row)!=1 or reference is None or row[0][0]!="android_marvin_01" or row[0][3] is not None:
 raise SystemExit("Unknown staged token identity")
if not hmac.compare_digest(row[0][1],reference[1]):
 raise SystemExit("Staging token verifier does not match production")
if reference[3] is not None or reference[2]<=time.time() or row[0][2]<=time.time():
 raise SystemExit("Original or staged token has expired")
statuses=live.execute("SELECT status,count(*) FROM tournaments GROUP BY status").fetchall()
if any(s not in ("planned","completed") for s,n in statuses if n):
 raise SystemExit("Production tournament is not idle")
for name in ("worker_inference_assignments","worker_inference_results","worker_update_holds"):
 if stage.execute("SELECT COUNT(*) FROM "+name).fetchone()[0]:
  raise SystemExit("Partial sandbox contains unfinished work: "+name)
pending=stage.execute(
 "SELECT COUNT(*) FROM worker_job_safety WHERE state!='idle_confirmed' OR job_id IS NOT NULL"
).fetchone()[0]
if pending:
 raise SystemExit("Sandbox contains unsafe worker state")
cp=stage.execute("SELECT payload_json FROM tournament_checkpoints WHERE tournament_id=1").fetchone()
if not cp or json.loads(cp[0]).get("moves")!=[]:
 raise SystemExit("The staging checkpoint was already modified")
print("RESUME_PREFLIGHT=PASS (one worker; isolated empty checkpoint; idle production)")
PY

[[ -z $(runuser -u debian -- git -C "$release" status --porcelain) ]] || {
  echo 'Staging tree contains local changes' >&2; exit 2;
}
if [[ $action == --preflight ]]; then
  exit 0
fi

installed_unit=0
installed_site=0
acl_added=0
rollback() {
  rc=$?
  if (( rc != 0 )); then
    echo 'SANDBOX_RESUME_ROLLBACK: staged unit/site removed; DB preserved' >&2
    if (( installed_site )); then
      rm -f "$site"
      caddy validate --config /etc/caddy/Caddyfile >/dev/null 2>&1 &&
        systemctl reload caddy || true
    fi
    if (( installed_unit )); then
      systemctl stop "$unit" || true
      rm -f "$unit_file"
      systemctl daemon-reload || true
    fi
    if (( acl_added )); then
      setfacl -x u:arena-phone-test /home/debian || true
    fi
  fi
  exit "$rc"
}
trap rollback EXIT

runuser -u debian -- git -C "$release" reset --hard "$sha"
acl_entries=$(getfacl -cp /home/debian)
existing=$(printf '%s\n' "$acl_entries" | awk -F: '$1=="user" && $2=="arena-phone-test" {print $0}')
if [[ -z "$existing" ]]; then
  acl_added=1
  setfacl -m u:arena-phone-test:--x /home/debian
elif [[ "$existing" != 'user:arena-phone-test:--x' ]]; then
  echo 'Unexpected existing staging ACL; refusing to overwrite' >&2
  exit 2
fi
runuser -u arena-phone-test -g arena-phone-test -G debian -- sh -c \
  'cd /home/debian/ai-chess-arena-phone-e2e-current && test -r remote_worker_phone_sandbox.py'
runuser -u arena-phone-test -g arena-phone-test -G debian -- "$python" -c 'import chess'

# Shorten only the isolated copy's lifetime, not the production token.
runuser -u arena-phone-test -g arena-phone-test -G debian -- "$python" - "$db" <<'PY'
import sqlite3,sys,time
with sqlite3.connect(sys.argv[1]) as db:
 db.execute("UPDATE worker_tokens SET expires_at=MIN(expires_at,?)",
            (int(time.time())+3600,))
PY

runuser -u debian -- git -C "$repo" show "$sha:systemd/ai-chess-arena-phone-e2e.service" > "$unit_file"
chmod 0644 "$unit_file"
chown root:root "$unit_file"
installed_unit=1
systemctl daemon-reload
systemctl start "$unit"

for n in 1 2 3 4 5 6 7 8; do
  if curl -fsS --max-time 3 http://127.0.0.1:8191/health |
      grep -Fq '"sandbox": true'; then break; fi
  sleep 1
done
curl -fsS --max-time 4 http://127.0.0.1:8191/health |
  grep -Fq '"sandbox": true' || exit 1

runuser -u debian -- git -C "$repo" show "$sha:caddy/ai-chess-phone-e2e.caddy" > "$site"
chmod 0644 "$site"
chown root:root "$site"
installed_site=1
caddy validate --config /etc/caddy/Caddyfile
systemctl reload caddy

verified=0
for n in 1 2 3 4 5 6 7 8 9 10 11 12; do
  if curl -fsS --max-time 10 "https://$domain/health" |
      grep -Fq '"sandbox": true'; then
    verified=1
    break
  fi
  sleep 2
done
[[ $verified == 1 ]] || { echo 'HTTPS stage unavailable' >&2; exit 1; }
# Reject invalid credentials through the HTTPS Worker API, without the
# normal web login redirect. This check never sends the real Worker token.
auth_body=$(mktemp)
auth_code=$(curl -sS --max-time 10 -o "$auth_body" -w '%{http_code}' \
  -H 'Content-Type: application/json' \
  -X POST "https://$domain/api/remote-workers/job-poll" \
  --data '{"worker_id":"android_marvin_01","token":"deliberately-invalid-staging-probe"}')
if [[ "$auth_code" != 401 ]] || grep -q 'login_url' "$auth_body"; then
  rm -f "$auth_body"
  echo "Staging Worker authentication probe failed (HTTP $auth_code)" >&2
  exit 1
fi
rm -f "$auth_body"
echo 'STAGING_INVALID_TOKEN_401=PASS'
echo "STAGING_HTTPS_READY=https://$domain"
echo 'PRODUCTION_SERVICE=UNCHANGED'
trap - EXIT
