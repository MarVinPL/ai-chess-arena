#!/usr/bin/env bash
# Reviewed rollout for Issue #272. Run only with SHA of merged main.
set -euo pipefail
repo=/home/debian/ai-chess-arena
db=/var/lib/ai-chess-arena/arena.sqlite3
unit=ai-chess-arena.service
dropin=/etc/systemd/system/ai-chess-arena.service.d/zzzzzzzzzzzz-issue272-worker-api.conf
managed=/home/debian/ai-chess-arena-live-b0759ee/managed-models
python=/home/debian/ai-chess-arena/.venv/bin/python

if [[ $# -ne 2 || ( "$1" != --preflight && "$1" != --deploy ) ]]; then
  echo "Usage: deploy_worker_phone_api_gateway.sh --preflight|--deploy <merged-main-sha>" >&2
  exit 2
fi
mode=$1
expected=$2
[[ $expected =~ ^[0-9a-f]{40}$ ]] || { echo 'Invalid main SHA' >&2; exit 2; }
[[ $(id -u) -eq 0 ]] || { echo 'Root required' >&2; exit 2; }
[[ -f $db && -f $repo/server.py && -x $python && -d $managed ]] ||
  { echo 'Production files missing; abort' >&2; exit 2; }
[[ ! -e $dropin ]] || { echo 'Gateway override already exists; abort' >&2; exit 2; }

runuser -u debian -- git -C "$repo" fetch --no-tags origin main
current=$(runuser -u debian -- git -C "$repo" rev-parse origin/main)
[[ $expected == "$current" ]] || { echo 'Main has moved; abort' >&2; exit 2; }
runuser -u debian -- git -C "$repo" show "$expected:systemd/ai-chess-arena-worker-api.conf" |
  grep -Fxq 'Environment=ARENA_PHONE_INFERENCE_ENABLED=0' ||
  { echo 'Review-gated inference-disabled unit is missing' >&2; exit 2; }

# Abort on any running/paused/unknown tournament or active remote lease.
"$python" - "$db" <<'PY'
import sqlite3,sys
db=sqlite3.connect("file:"+sys.argv[1]+"?mode=ro",uri=True)
states=db.execute("SELECT status,COUNT(*) FROM tournaments GROUP BY status").fetchall()
print("Tournament states:",states)
if any(state not in ("planned","completed") for state,n in states if n):
    raise SystemExit("LIVE_TOURNAMENT: refusing deployment")
for table in ("worker_job_safety","worker_inference_assignments",
              "worker_update_holds","worker_update_pause_requests",
              "worker_update_resume"):
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone():
        if table=="worker_job_safety":
            count=db.execute("SELECT COUNT(*) FROM worker_job_safety "
                "WHERE state!='idle_confirmed' OR job_id IS NOT NULL").fetchone()[0]
        else:
            count=db.execute("SELECT COUNT(*) FROM "+table).fetchone()[0]
        if count: raise SystemExit("WORKER_UNSAFE: "+table)
print("IDLE_PRECHECK=PASS")
PY

previous=$(systemctl show --property=WorkingDirectory --value "$unit")
[[ $previous == /home/debian/ai-chess-arena-live-* && -f $previous/server.py ]] ||
  { echo 'Unknown deployed release; abort' >&2; exit 2; }
systemctl is-active --quiet "$unit" || { echo 'Service inactive; abort' >&2; exit 2; }
short=$(printf '%s' "$expected" | cut -c 1-8)
target=/home/debian/ai-chess-arena-live-workerapi-$short
[[ ! -e $target ]] || { echo 'Deployment target already exists; abort' >&2; exit 2; }

if [[ "$mode" == --preflight ]]; then
  echo "PRECHECK_SUCCESS: safe idle state; main pinned to reviewed SHA."
  exit 0
fi

# Keep the existing unit intact until new source and symlinks exist.
runuser -u debian -- git -C "$repo" worktree add --detach "$target" "$expected"
ln -s "$db" "$target/arena.sqlite3"
ln -s "$managed" "$target/managed-models"
chown -h debian:debian "$target/arena.sqlite3" "$target/managed-models"
"$python" -m py_compile "$target/server.py" "$target/remote_worker_phone_api.py"

installed=0
rollback() {
  result=$?
  if (( result != 0 && installed == 1 )); then
    echo 'ROLLBACK: restoring previous service configuration' >&2
    rm -f "$dropin"
    systemctl daemon-reload || true
    systemctl restart "$unit" || true
  fi
  exit "$result"
}
trap rollback EXIT

# Install config from MERGED git main, never from unreviewed working files.
install -m 0644 -o root -g root \
  "$target/systemd/ai-chess-arena-worker-api.conf" "$dropin"
cat >> "$dropin" <<UNIT
WorkingDirectory=$target
ExecStart=
ExecStart=$python $target/server.py
BindPaths=
BindPaths=$target
ReadWritePaths=
ReadWritePaths=$target
ReadWritePaths=$managed
ReadWritePaths=/var/lib/ai-chess-arena
UNIT
installed=1
systemctl daemon-reload
systemctl restart "$unit"

# Validate code path WITHOUT accessing the real token or disclosing secrets.
for attempt in 1 2 3 4 5 6 7 8; do
  status=$(curl -sS --max-time 4 \
    -o /tmp/arena-worker-gateway-272-check.json \
    -w '%{http_code}' -H 'Content-Type: application/json' \
    -d '{"worker_id":"android_marvin_01","token":"not-a-real-token-00000000000000000000","nonce":"0123456789abcdef0123456789abcdef"}' \
    http://127.0.0.1:8090/api/remote-workers/job-poll) || status=000
  if [[ $status == 401 ]] &&
      ! grep -Fq 'login_url' /tmp/arena-worker-gateway-272-check.json; then
    rm -f /tmp/arena-worker-gateway-272-check.json
    echo 'DEPLOY_SUCCESS: machine token required; no web login gateway.'
    trap - EXIT
    exit 0
  fi
  sleep 1
done
echo 'API did not pass post-restart verification' >&2
exit 1
