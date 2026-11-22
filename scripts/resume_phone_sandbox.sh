#!/usr/bin/env bash
# Issue #282: resume an already bootstrapped, isolated phone E2E staging.
# Never touches production state or creates another token.
set -euo pipefail
[[ $# == 2 && $1 == --preflight || $# == 2 && $1 == --resume ]] || { echo 'Usage: --preflight|--resume <merged-main-sha>' >&2; exit 2; }
mode=$1; sha=$2
[[ $sha =~ ^[0-9a-f]{40}$ && $(id -u) == 0 ]] || { echo 'Root and exact SHA required' >&2; exit 2; }
repo=/home/debian/ai-chess-arena
stage=/var/lib/ai-chess-arena-phone-e2e
db=$stage/arena.sqlite3
link=/home/debian/ai-chess-arena-phone-e2e-current
unit=ai-chess-arena-phone-e2e.service
unit_file=/etc/systemd/system/$unit
caddy_file=/etc/caddy/sites/ai-chess-phone-e2e.caddy
acl_marker=/etc/ai-chess-phone-e2e-acl-owned
domain=arena-worker-test.148-113-182-137.sslip.io
python=/home/debian/ai-chess-arena/.venv/bin/python
test_user=arena-phone-test

# Verify this is the precise partially-installed staging described in #282.
runuser -u debian -- git -C "$repo" fetch --no-tags origin main
[[ $(runuser -u debian -- git -C "$repo" rev-parse origin/main) == "$sha" ]] || { echo 'main SHA mismatch' >&2; exit 2; }
[[ -L $link && -f $db && -d $stage && ! -e $unit_file && ! -e $caddy_file ]] || { echo 'Unexpected partial deployment state' >&2; exit 2; }
work=$(readlink -f -- "$link")
[[ $work == /home/debian/ai-chess-arena-phone-e2e-* && -f $work/remote_worker_phone_sandbox.py ]] || { echo 'Unexpected test worktree' >&2; exit 2; }
[[ $(runuser -u debian -- git -C "$work" rev-parse HEAD) == 358164beda866c05e34ac20af9a4450f320219c0 ]] || { echo 'Unexpected original staging worktree' >&2; exit 2; }
[[ $(stat -c %a "$stage") == 700 && $(stat -c %a "$db") == 600 ]] || { echo 'Unsafe staging permissions' >&2; exit 2; }
uid=$(id -u "$test_user")
[[ $(stat -c %u "$db") == "$uid" && $(stat -c %u "$stage") == "$uid" ]] || { echo 'Unexpected staging owner' >&2; exit 2; }
[[ ! -e $acl_marker ]] || { echo 'ACL marker already exists; inspect before resuming' >&2; exit 2; }
! systemctl is-active --quiet "$unit" || { echo 'Staging already active' >&2; exit 2; }
systemctl is-active --quiet ai-chess-arena.service || { echo 'Production service not active' >&2; exit 2; }
[[ $(ss -ltn '( sport = :8191 )' | wc -l) == 1 ]] || { echo 'Staging port occupied' >&2; exit 2; }
command -v setfacl >/dev/null
command -v getfacl >/dev/null
! getfacl -cp /home/debian | grep -q "^user:$test_user:" || { echo 'Existing ACL: manual inspection required' >&2; exit 2; }
"$python" - "$db" /var/lib/ai-chess-arena/arena.sqlite3 <<'PY'
import os,sqlite3,sys,time
stage,prod=sys.argv[1:]
assert os.path.realpath(stage)!=os.path.realpath(prod)
with sqlite3.connect(f"file:{stage}?mode=ro",uri=True) as c:
    assert c.execute("SELECT COUNT(*) FROM worker_tokens WHERE worker_id='android_marvin_01' AND revoked_at IS NULL AND expires_at>?",(time.time(),)).fetchone()[0]==1
    for table in ("worker_inference_assignments","worker_inference_results"):
        assert c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]==0
    assert c.execute("SELECT COUNT(*) FROM worker_job_safety WHERE state NOT IN ('idle_confirmed','unknown') OR job_id IS NOT NULL").fetchone()[0]==0
with sqlite3.connect(f"file:{prod}?mode=ro",uri=True) as c:
    assert all(status in ("planned","completed") for status,count in c.execute("SELECT status,count(*) FROM tournaments GROUP BY status") if count)
print("PHONE_SANDBOX_RESUME_PREFLIGHT_PASS")
PY
if [[ $mode == --preflight ]]; then exit 0; fi

acl_added=0; unit_added=0; caddy_added=0
rollback() {
  rc=$?
  trap - EXIT
  if (( rc != 0 )); then
    if (( caddy_added )); then
      rm -f "$caddy_file"
      caddy validate --config /etc/caddy/Caddyfile >/dev/null 2>&1 && systemctl reload caddy || true
    fi
    if (( unit_added )); then
      systemctl stop "$unit" || true
      rm -f "$unit_file"
      systemctl daemon-reload || true
    fi
    if (( acl_added )); then
      setfacl -x "u:$test_user" /home/debian || true
      rm -f "$acl_marker"
    fi
    echo "PHONE_SANDBOX_RESUME_ROLLBACK" >&2
  fi
  exit "$rc"
}
trap rollback EXIT
setfacl -m "u:$test_user:--x" /home/debian
acl_added=1
install -m 0600 -o root -g root /dev/null "$acl_marker"
runuser -u "$test_user" -g "$test_user" -G debian -- test -x /home/debian
runuser -u "$test_user" -g "$test_user" -G debian -- test ! -r /home/debian
runuser -u "$test_user" -g "$test_user" -G debian -- test -r "$work/remote_worker_phone_sandbox.py"
# Limit only the STAGING verifier, never the production token.
runuser -u "$test_user" -g "$test_user" -G debian -- "$python" - "$db" <<'PY'
import sqlite3,sys,time
with sqlite3.connect(sys.argv[1]) as c:
    cutoff=int(time.time())+3600
    c.execute("UPDATE worker_tokens SET expires_at=MIN(expires_at,?) WHERE worker_id='android_marvin_01' AND revoked_at IS NULL",(cutoff,))
    assert c.total_changes==1
PY
install -m 0644 -o root -g root "$work/systemd/ai-chess-arena-phone-e2e.service" "$unit_file"
unit_added=1
systemctl daemon-reload
systemctl start "$unit"
for _ in {1..10}; do
  if curl -fsS --max-time 3 http://127.0.0.1:8191/health | grep -Fq '"sandbox": true'; then break; fi
  sleep 1
done
curl -fsS --max-time 5 http://127.0.0.1:8191/health | grep -Fq '"sandbox": true'
install -m 0644 -o root -g root "$work/caddy/ai-chess-phone-e2e.caddy" "$caddy_file"
caddy_added=1
caddy validate --config /etc/caddy/Caddyfile
systemctl reload caddy
curl -fsS --retry 10 --retry-delay 2 --max-time 10 "https://$domain/health" | grep -Fq '"sandbox": true'
code=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 10 -X POST "https://$domain/api/remote-workers/job-poll" -H 'Content-Type: application/json' --data '{"worker_id":"android_marvin_01","token":"invalid"}')
[[ $code == 401 ]] || { echo "Invalid-token status: $code (expected 401)" >&2; exit 1; }
trap - EXIT
echo "PHONE_SANDBOX_HTTPS_READY https://$domain"
