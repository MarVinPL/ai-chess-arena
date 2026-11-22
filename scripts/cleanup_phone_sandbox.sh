#!/usr/bin/env bash
# Issue #275: destroy isolated phone test only AFTER a finished checkpoint.
set -euo pipefail
[[ $# -eq 1 && "$1" == --after-finalize ]] || {
  echo "Usage: $0 --after-finalize (only after confirmed result)" >&2
  exit 2
}
[[ $(id -u) == 0 ]] || { echo 'root required' >&2; exit 2; }

repo=/home/debian/ai-chess-arena
db=/var/lib/ai-chess-arena-phone-e2e/arena.sqlite3
dir=/var/lib/ai-chess-arena-phone-e2e
unit=ai-chess-arena-phone-e2e.service
unit_file=/etc/systemd/system/$unit
caddy_file=/etc/caddy/sites/ai-chess-phone-e2e.caddy
link=/home/debian/ai-chess-arena-phone-e2e-current

[[ -f "$db" && -L "$link" ]] || { echo 'No complete sandbox deployment'; exit 2; }

python3 - "$db" <<'PY'
import sqlite3,sys,json
db=sqlite3.connect("file:"+sys.argv[1]+"?mode=ro",uri=True)
moves=db.execute(
    "SELECT payload_json FROM tournament_checkpoints WHERE tournament_id=1"
).fetchone()
if moves is None or len(json.loads(moves[0]).get("moves",[]))!=1:
    raise SystemExit("Refusing cleanup before one committed test move")
for table in ("worker_inference_assignments", "worker_inference_results"):
    if db.execute("SELECT COUNT(*) FROM "+table).fetchone()[0]:
        raise SystemExit("Refusing cleanup with pending data: "+table)
if db.execute("SELECT COUNT(*) FROM worker_job_safety WHERE state!='idle_confirmed' OR job_id IS NOT NULL").fetchone()[0]:
    raise SystemExit("Refusing cleanup with unsafe Worker state")
print("SANDBOX_CLEANUP_PRECHECK=PASS")
PY
systemctl stop "$unit"
if [[ -e "$caddy_file" ]]; then
  mv "$caddy_file" "$caddy_file.disabled"
  if ! caddy validate --config /etc/caddy/Caddyfile; then
    mv "$caddy_file.disabled" "$caddy_file"
    systemctl start "$unit"
    exit 1
  fi
  systemctl reload caddy
  rm -f "$caddy_file.disabled"
fi
rm -f "$unit_file"
systemctl daemon-reload
release=$(readlink -f "$link")
rm -f "$link"
case "$release" in
  /home/debian/ai-chess-arena-phone-e2e-*)
    runuser -u debian -- git -C "$repo" worktree remove "$release"
    ;;
  *) echo 'Unknown test release path; keeping it' >&2 ;;
esac
rm -f "$db"
rmdir "$dir"
# Undo the narrow test-only traversal ACL before deleting the UID, otherwise
# an obsolete numerical ACL principal would remain on the user's home.
if getfacl -cp /home/debian | grep -Fxq 'user:arena-phone-test:--x'; then
  setfacl -x u:arena-phone-test /home/debian
fi
userdel arena-phone-test
echo "SANDBOX_DATA_PURGED, PRODUCTION_UNCHANGED"
