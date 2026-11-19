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

## Emergency recovery if the rootfs is missing or damaged

This section is a **controlled recovery procedure**, not permission to create a replacement automatically. A missing machine entry in `machinectl` is not enough to trigger it: first follow **Discovery — read-only** and verify whether `/var/lib/machines/arena-test` actually exists.

### Current backup status

As of 2026-11-19, the repository contains evidence that the existing rootfs was reused, but it does **not** establish that a restorable backup/archive of the `arena-test` rootfs exists, where such a backup is stored, or what its digest is. Do not assume that a backup exists. Before any recovery, the owner/operator must identify an actual backup artifact and its independently trusted integrity record. If no such artifact can be identified, stop and report `NOT_VERIFIED`; do not build a fresh Debian image and call it a restoration.

### Recovery gates — all required

1. Obtain explicit owner approval for this specific restoration. Documentation or a previous approval to start the preserved image is not approval to create or restore an image.
2. Identify the exact backup artifact and its origin (backup job, timestamp, source machine, and expected Debian 13/trixie identity). The artifact must come from an approved backup location, not an arbitrary public image or an unverified download.
3. Obtain the expected SHA-256 (or stronger signed manifest) through a trusted channel independent of the artifact itself. A checksum file stored beside an untrusted archive is not independent verification.
4. Confirm that the artifact is a complete rootfs backup in a documented format that preserves numeric ownership, permissions, symlinks, hardlinks, ACLs, xattrs, and required filesystem metadata. If the format or completeness is unknown, stop.
5. Confirm sufficient free space for both the archive and a separate staging extraction, and obtain an explicit resource/isolation profile for the recovery validation. The existing machine currently has no explicit `MemoryMax` or `CPUQuota`; do not invent limits.
6. Confirm that the target `/var/lib/machines/arena-test` is absent. If it exists but is damaged, do not overwrite, delete, or move it without a separately approved preservation plan.

### Restore from an approved tar archive (only if that is the documented backup format)

The following is a template. Replace the placeholders only with values independently verified under the gates above. Do not execute it with guessed paths, digests, or archive format. Run as an authorized administrator during an approved maintenance window.

First verify the archive digest against the independently trusted expected value:

```sh
sha256sum /approved/backup/location/arena-test-rootfs.tar
# Compare the output to the expected SHA-256 obtained independently.
```

Do not continue unless it matches exactly. Then extract into a new staging directory on the same filesystem, preserving metadata; do not extract directly over the final machine path:

```sh
sudo test ! -e /var/lib/machines/.arena-test-restore-staging && \
  sudo install -d -m 0700 /var/lib/machines/.arena-test-restore-staging
sudo tar --numeric-owner --same-owner --acls --xattrs --xattrs-include='*'  \
  -xpf /approved/backup/location/arena-test-rootfs.tar  \
  -C /var/lib/machines/.arena-test-restore-staging
```

The archive's documented layout must be checked first: the commands above assume its contents are the root of the filesystem, not a parent directory containing another nested `arena-test/` folder. Do not improvise extraction flags for a different format. If extraction fails or metadata preservation cannot be demonstrated, stop and preserve the staging directory for investigation; do not promote it.

Before promotion, perform read-only checks on the staging tree: confirm `/etc/os-release` identifies Debian GNU/Linux 13 (trixie), required system directories exist, symlinks resolve within the intended rootfs, and there are no unexpected mount points, host bind paths, credentials, or production data. Check ownership, ACLs, xattrs, and the extracted tree against the backup manifest. Do not execute untrusted binaries from the restored tree on the host.

Only after the owner has approved the exact verified artifact and the staging checks pass may an administrator promote the staging tree to `/var/lib/machines/arena-test`. The final path must not already exist, and one operator must hold an exclusive maintenance window so no concurrent process can create it between the check and rename. Promotion must be an atomic same-filesystem rename; never merge the restored tree into an existing directory:

```sh
sudo test ! -e /var/lib/machines/arena-test && \
  sudo mv -T -- /var/lib/machines/.arena-test-restore-staging /var/lib/machines/arena-test
```

Record the source artifact, trusted digest, operator, approval, timestamps, and validation evidence in the relevant Issue/PR. If any precondition is false, do not promote. If the rename command fails, stop and inspect both paths; do not retry with a different copy/merge command.

### First boot after an approved restoration

1. Re-run the discovery checks and verify the final rootfs identity and Debian 13 release.
2. Start it only through the documented `systemd-nspawn` invocation with `--private-network --boot`; do not add host bind mounts, shared production sockets, or credentials.
3. Verify the runtime with `machinectl status`, `systemd-detect-virt --container`, and `/etc/os-release` from inside the machine.
4. Verify the machine's network isolation, cgroup settings, resource budget, and that production databases, API sockets, model directories, and credentials are inaccessible.
5. Keep model execution blocked until a separately reviewed resource profile is configured and a fresh preflight passes. Booting the restored container does not authorize model downloads, runtime installation, inference, or legal-move tests.
6. If identity, isolation, or resource checks fail, power off the machine and report `NOT_VERIFIED`. Do not fall back to host execution or another container technology.

### If no valid backup exists

Stop. Do not run `debootstrap`, `machinectl pull`, download a Debian image, clone a random rootfs, or create a new LXC/Docker/VM/container as a substitute. A newly built rootfs is not the previously approved disposable environment and requires a separate owner decision, security review, resource profile, and new Issue/PR before it can be considered. Until then, model execution remains `NOT_VERIFIED`.

## Restoration evidence — 2026-11-19

- Prior evidence was located in PR #173, especially `docs/deployment/issue-157-worker-crash-evidence-2026-11-18.json` and `docs/runtime-containment-design.md` in that PR's branch.
- `sudo -n ls -lah /var/lib/machines/arena-test` confirmed the preserved rootfs (about 306 MiB at inspection time).
- The existing image was started with the command above. `machinectl list` registered `arena-test`; `machinectl status` identified Debian 13 and the root `/var/lib/machines/arena-test`.
- `systemd-detect-virt --container` executed through `systemd-run --machine=arena-test` reported `systemd-nspawn`; `/etc/os-release` reported Debian 13.
- The container boot used `--private-network`. No model weights were downloaded, no runtime was installed, no model was loaded, and no inference or legal-move test was run as part of restoration.
- The host production API and production database were not changed by this restoration.
