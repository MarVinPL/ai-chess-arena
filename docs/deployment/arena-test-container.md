# Existing `arena-test` systemd-nspawn container

This runbook describes how to find, start, verify, and stop the **existing** disposable model-test environment on the Arena VPS. It does not create or rebuild an image and does not authorize model downloads or inference.

## Identity and provenance

- Machine name: `arena-test`
- Runtime: `systemd-nspawn` (not LXC)
- OS: Debian GNU/Linux 13 (trixie)
- Existing root filesystem: `/var/lib/machines/arena-test`
- Network mode: private network (`--private-network`)
- Prior verified use: [PR #173](https://github.com/MarVinPL/ai-chess-arena/pull/173), including the 2026-11-18 worker-crash/recovery evidence. That PR records that the existing image was reused and powered off after the test.

The 2026-11-19 recovery procedure was based on the prior PR evidence, then confirmed against the live VPS with `machinectl list`, `machinectl status`, and a privileged listing of `/var/lib/machines`. The rootfs was present and was started in place; no image was created or modified for restoration.

## Discovery — read-only

Run these checks before attempting to start anything:

```sh
machinectl list --all --no-pager
sudo -n ls -lah /var/lib/machines
sudo -n test -d /var/lib/machines/arena-test
sudo -n ls -lah /var/lib/machines/arena-test
machinectl status arena-test --no-pager
```

Interpretation:

- `machinectl list` shows whether the machine is currently registered/running; an absent entry alone does **not** prove that its image is missing.
- `/var/lib/machines/arena-test` is the persistent rootfs. Verify this exact directory exists and is a directory.
- If the image is absent, unreadable, or does not identify as Debian 13, stop. Do not use `debootstrap`, `machinectl pull`, `systemd-nspawn` with a new directory, LXC, Docker, or a VM as a fallback.
- Do not infer absence from `lxc-info` or `lxc-ls`; this machine is not LXC-managed.

## Start the existing image

Only after the discovery checks identify the existing image, start it with its private network and full system boot:

```sh
sudo -n systemd-run \
  --unit=arena-test-container.service \
  --collect \
  --property=Restart=no \
  /usr/bin/systemd-nspawn \
    --directory=/var/lib/machines/arena-test \
    --machine=arena-test \
    --private-network \
    --boot
```

This runs the existing rootfs in place. The command-line options are the canonical configuration for this project; no host `/etc/systemd/nspawn/arena-test.nspawn` file is required or installed. Do not weaken the private-network setting.

## Verify identity, state, and resources

```sh
machinectl list --all --no-pager
machinectl status arena-test --no-pager
sudo -n systemd-run --machine=arena-test --wait --pipe --quiet \
  /usr/bin/systemd-detect-virt --container
sudo -n systemd-run --machine=arena-test --wait --pipe --quiet \
  /usr/bin/cat /etc/os-release
free -h
systemctl show 'machine-arena\x2dtest.scope' \
  -p ControlGroup -p MemoryCurrent -p MemoryMax \
  -p CPUQuotaPerSecUSec -p TasksCurrent
systemctl show arena-test-container.service \
  -p ActiveState -p SubState -p MemoryCurrent -p MemoryMax
```

The identity check must report `systemd-nspawn`, and `/etc/os-release` must identify Debian 13. Confirm the machine is running and inspect host `MemAvailable`, disk free space, and effective cgroup limits before every model test. Do not assume the container has a safe memory limit: the restored machine currently has no explicit `MemoryMax` at the machine scope. Set and verify an appropriate per-test resource limit only as part of a separately authorized test plan; never guess a limit or start a model when the resource budget is insufficient.

The machine being booted is not a model test. Do not download artifacts, install runtimes, or start inference until the exact candidate, pinned artifact, resource budget, and separate execution authorization have been checked.

## Stop and verify shutdown

After authorized test work is complete:

```sh
sudo -n machinectl poweroff arena-test
machinectl list --all --no-pager
machinectl status arena-test --no-pager
systemctl status arena-test-container.service --no-pager
```

Confirm the machine is no longer running and its transient service has exited/been collected. If graceful shutdown does not complete, stop and investigate; do not kill arbitrary host processes or delete the rootfs. Preserve the existing image for later reuse.

## Restoration evidence — 2026-11-19

- Prior evidence was located in PR #173, especially `docs/deployment/issue-157-worker-crash-evidence-2026-11-18.json` and `docs/runtime-containment-design.md` in that PR's branch.
- `sudo -n ls -lah /var/lib/machines/arena-test` confirmed the preserved rootfs (about 306 MiB at inspection time).
- The existing image was started with the command above. `machinectl list` registered `arena-test`; `machinectl status` identified Debian 13 and the root `/var/lib/machines/arena-test`.
- `systemd-detect-virt --container` executed through `systemd-run --machine=arena-test` reported `systemd-nspawn`; `/etc/os-release` reported Debian 13.
- The container boot used `--private-network`. No model weights were downloaded, no runtime was installed, no model was loaded, and no inference or legal-move test was run as part of restoration.
- The host production API and production database were not changed by this restoration.
