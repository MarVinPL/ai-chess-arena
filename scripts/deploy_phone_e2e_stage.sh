#!/usr/bin/env bash
# Issue #276: one-shot, temporary Android HTTPS E2E service, never production.
set -euo pipefail
repo=/home/debian/ai-chess-arena
python=/home/debian/ai-chess-arena/.venv/bin/python
source_db=/var/lib/ai-chess-arena/arena.sqlite3
stage_dir=/var/lib/ai-chess-arena-phone-e2e
stage_db=/var/lib/ai-chess-arena-phone-e2e/phone-stage.sqlite3
site=/etc/caddy/sites/ai-chess-worker-e2e.caddy
service=ai-chess-arena-phone-e2e
domain=arena-worker-test.148-113-182-137.sslip.io
worker_id=android_marvin_01
model_sha=6f85a640a97cf2bf5b8e764087b1e83da0fdb51d7c9fab7d0fece9385611df83

if [[ $# -ne 2 || ( "$1" != "--preflight" && "$1" != "--deploy" ) ]]; then
  echo 'Usage: deploy_phone_e2e_stage.sh --preflight|--deploy <merged-main-sha>' >&2
  exit 2
fi
mode=$1
sha=$2
[[ $sha =~ ^[0-9a-f]{40}$ ]] || { echo 'Invalid pinned SHA' >&2; exit 2; }
[[ $(id -u) == 0 ]] || { echo 'Need root privileges' >&2; exit 2; }
[[ -r $source_db && -x $python && -d /etc/caddy/sites ]] ||
  { echo 'Missing deployment prerequisites' >&2; exit 2; }
[[ ! -e $stage_dir && ! -e $site ]] ||
  { echo 'Existing stage state: refuse overwrite/restart' >&2; exit 2; }
systemctl is-active --quiet ai-chess-arena.service || {
  echo 'Production service must be healthy' >&2; exit 2;
}
if systemctl is-active --quiet "$service.service"; then
  echo 'Test stage is already running' >&2
  exit 2
fi
if ss -lnt | grep -q '127.0.0.1:8789'; then
  echo 'Test port already in use' >&2
  exit 2
fi
runuser -u debian -- git -C "$repo" fetch --no-tags origin main
main=$(runuser -u debian -- git -C "$repo" rev-parse origin/main)
[[ "$main" == "$sha" ]] || { echo 'main moved; no deployment' >&2; exit 2; }
runuser -u debian -- git -C "$repo" cat-file -e "$sha:remote_worker_phone_stage.py"
runuser -u debian -- git -C "$repo" cat-file -e "$sha:ops/arena-worker-test.caddy"
"$python" - "$source_db" "$worker_id" "$model_sha" <<'PY'
import sqlite3,sys,time
db=sqlite3.connect("file:"+sys.argv[1]+"?mode=ro",uri=True)
states=db.execute("SELECT status,count(*) FROM tournaments GROUP BY status").fetchall()
if any(state not in ("planned","completed") for state,count in states if count):
  raise SystemExit("Active production tournament; NO DEPLOY")
row=db.execute("SELECT expires_at,revoked_at FROM worker_tokens WHERE worker_id=?",
               (sys.argv[2],)).fetchone()
if not row or row[1] is not None or row[0] <= time.time():
  raise SystemExit("Invalid original Worker token")
print("PRODUCTION_READ_ONLY=PASS; token exists, no secret disclosed")
PY

if [[ $mode == "--preflight" ]]; then
  echo "STAGE_PREFLIGHT=PASS"
  exit 0
fi

work=/home/debian/ai-chess-arena-live-phone-e2e-$(printf '%s' "$sha" | cut -c1-8)
[[ ! -e $work ]] || { echo 'Staging worktree already exists' >&2; exit 2; }
installed=0
service_started=0
rollback() {
  exitcode=$?
  if (( exitcode != 0 )); then
    echo 'STAGE_ROLLBACK: revert only E2E staging assets' >&2
    if (( installed )); then
      rm -f "$site"
      caddy validate --config /etc/caddy/Caddyfile >/dev/null 2>&1 && systemctl reload caddy || true
    fi
    if (( service_started )); then
      systemctl stop "$service.service" || true
    fi
    if [[ -f $stage_db ]]; then
      rm -f "$stage_db" "$stage_db-wal" "$stage_db-shm" "$stage_db-journal"
    fi
    rmdir "$stage_dir" 2>/dev/null || true
  fi
  exit "$exitcode"
}
trap rollback EXIT

runuser -u debian -- git -C "$repo" worktree add --detach "$work" "$sha"
install -d -o debian -g debian -m 0700 "$stage_dir"
runuser -u debian -- "$python" "$work/remote_worker_phone_stage.py" prepare \
  --db "$stage_db" --source-db "$source_db" \
  --worker-id "$worker_id" --expected-sha "$model_sha"
runuser -u debian -- "$python" "$work/remote_worker_phone_stage.py" arm \
  --db "$stage_db"

systemd-run --unit="$service" \
  --property=User=debian --property=Group=debian \
  --property=WorkingDirectory="$work" \
  --property=NoNewPrivileges=true --property=PrivateTmp=true \
  --property=ProtectSystem=full --property=ProtectHome=read-only \
  --property=InaccessiblePaths=/var/lib/ai-chess-arena \
  --property=ReadWritePaths="$stage_dir" \
  -- "$python" "$work/remote_worker_phone_stage.py" serve \
    --db "$stage_db" --port 8789
service_started=1
for try in 1 2 3 4 5; do
  curl -fsS --max-time 3 http://127.0.0.1:8789/health >/dev/null && break
  sleep 1
done
curl -fsS --max-time 4 http://127.0.0.1:8789/health >/dev/null

install -m 0644 -o root -g root "$work/ops/arena-worker-test.caddy" "$site"
installed=1
caddy validate --config /etc/caddy/Caddyfile
systemctl reload caddy
# ACME may issue the new free TLS certificate asynchronously. A failed
# public probe must never be mistaken for a successful HTTPS readiness.
code=$(curl -sS --max-time 15 --output /tmp/issue276-stage-health.json \
  --write-out '%{http_code}' "https://$domain/health") || code=000
if [[ $code != 200 ]]; then
  echo 'Stage TLS not ready; the operator must inspect Caddy before phone use' >&2
  exit 1
fi
rm -f /tmp/issue276-stage-health.json
echo "STAGE_READY_HTTPS=https://$domain"
echo "STAGE_DB=$stage_db"
trap - EXIT
