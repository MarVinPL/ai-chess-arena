# Controlled handoff to the dedicated Arena systemd unit

This runbook transfers the existing API process to deploy/systemd/ai-chess-arena.service. It is operational documentation, not an installer. Merging the unit does not install, enable, or start it.

## Safety gates

Do not begin unless all are true:
- Separate approval covers the handoff and deployment.
- The exact target commit and application directory have been reviewed.
- The API reports tournament_running=false and running=false.
- A SQLite backup made through SQLite's backup API passes PRAGMA integrity_check and PRAGMA foreign_key_check.
- Current index.html, all local backups, and checkout state are preserved.
- No model download, model-directory change, DB reset, or tournament action is in scope.

Never restart, stop, reload, or signal sentinelx-cloud-core.service. Never use pkill, killall, broad pgrep matches, or a guessed PID.

## 1. Verify and install the unit without starting it

From the reviewed application checkout:

    sudo systemd-analyze verify deploy/systemd/ai-chess-arena.service
    sudo install -o root -g root -m 0644 deploy/systemd/ai-chess-arena.service /etc/systemd/system/ai-chess-arena.service
    sudo systemctl daemon-reload
    sudo systemctl show ai-chess-arena.service -p LoadState -p ActiveState -p MainPID -p ExecStart -p ControlGroup

Do not enable or start it at this stage.

## 2. Revalidate the exact old process

Read the API state again. If either running flag is true, stop. Confirm port 8090 has exactly one owner and inspect:

    curl --fail --silent --show-error http://127.0.0.1:8090/api/state
    sudo ss -ltnp 'sport = :8090'
    ps -p <PID> -o pid,ppid,user,lstart,stat,args
    sudo readlink -f /proc/<PID>/cwd
    sudo tr '\0' ' ' </proc/<PID>/cmdline; echo
    sudo cat /proc/<PID>/cgroup

Proceed only if the single process is exactly:
- executable /home/debian/ai-chess-arena/.venv/bin/python;
- arguments server.py only;
- working directory /home/debian/ai-chess-arena;
- user debian;
- cgroup /system.slice/sentinelx-cloud-core.service;
- API reports no active game and no active tournament.

Any mismatch means stop and investigate.

## 3. Stop only the verified PID and start the dedicated unit

Set ARENA_PID to the just-verified PID. Recheck identity immediately before SIGTERM:

    ARENA_PID=<verified-pid>
    sudo python3 - "$ARENA_PID" <<'PY'
    import os, pwd, signal, sys
    pid = int(sys.argv[1])
    base = f"/proc/{pid}"
    with open(base + "/cmdline", "rb") as f:
        argv = [x.decode() for x in f.read().split(b"\0") if x]
    cwd = os.path.realpath(base + "/cwd")
    status = open(base + "/status", encoding="utf-8").read()
    uid_line = next(line for line in status.splitlines() if line.startswith("Uid:"))
    if argv != ["/home/debian/ai-chess-arena/.venv/bin/python", "server.py"]:
        raise SystemExit("REFUSED: unexpected command line")
    if cwd != "/home/debian/ai-chess-arena":
        raise SystemExit("REFUSED: unexpected working directory")
    if int(uid_line.split()[1]) != pwd.getpwnam("debian").pw_uid:
        raise SystemExit("REFUSED: process is not owned by debian")
    cgroup = open(base + "/cgroup", encoding="utf-8").read()
    if "/system.slice/sentinelx-cloud-core.service" not in cgroup:
        raise SystemExit("REFUSED: unexpected cgroup")
    os.kill(pid, signal.SIGTERM)
    print(f"SIGTERM sent only to verified Arena PID {pid}")
    PY

Wait for that PID to exit and verify port 8090 is free. If it does not exit within the shutdown window, do not escalate automatically to SIGKILL; stop and investigate. Never touch the SentinelX PID or unit.

Only after the port is free:

    sudo systemctl start ai-chess-arena.service
    sudo systemctl status ai-chess-arena.service --no-pager
    curl --fail --silent --show-error http://127.0.0.1:8090/api/state
    curl --fail --silent --show-error http://127.0.0.1:8090/api/live

Confirm the API PID belongs to ai-chess-arena.service and its cgroup is /system.slice/ai-chess-arena.service. Confirm SentinelX remains active and its MainPID is unchanged. Do not start a tournament as a smoke test.

Enable at boot only after all checks pass and separate approval covers persistence:

    sudo systemctl enable ai-chess-arena.service

## 4. Rollback

Rollback requires separate approval. Stop only ai-chess-arena.service, never SentinelX. Preserve logs and the failed deployment. Restore the reviewed application snapshot and, only if a migration changed it, the verified SQLite backup. Re-run integrity checks before starting the service. Do not relaunch the old process with nohup or by attaching it to SentinelX.

If the dedicated service cannot be started safely, leave Arena stopped and preserve remote access. Do not improvise with a broad process kill or restart of another service.

## Scope boundary

This handoff does not start, resume, reset, or alter tournaments #4 or #5. It does not change model files, the shared model directory, or tournament rows. KillMode=control-group applies only to processes launched by the dedicated Arena unit.
