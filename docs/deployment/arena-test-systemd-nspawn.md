# Disposable `arena-test` execution environment

## Canonical environment

The existing test environment on the VPS is **systemd-nspawn**, not LXC:

- Machine name: `arena-test`
- Existing root filesystem: `/var/lib/machines/arena-test`
- OS: Debian GNU/Linux 13 (trixie)
- Container runtime: `systemd-nspawn` (systemd 257 on the recorded host)
- Network isolation: `--private-network`
- Image lifecycle: reuse the existing image; never create, clone, bootstrap, or replace it as an automatic fallback.

The image was discovered while investigating the earlier `arena-test` execution evidence in PR #173. The machine-readable record `docs/deployment/issue-157-worker-crash-evidence-2026-11-18.json` identifies the root filesystem and runtime. The accompanying `docs/runtime-containment-design.md` records the prior integration test. The root filesystem itself remains at `/var/lib/machines/arena-test` after shutdown; a powered-off machine is not a missing image.

## Read-only discovery

Run these checks on the VPS before starting anything:

```sh
sudo lxc-info -n arena-test                 # Informational only; this is not an LXC machine
sudo machinectl list --all --no-pager
sudo machinectl status arena-test --no-pager
sudo ls -ld /var/lib/machines/arena-test
sudo cat /var/lib/machines/arena-test/etc/os-release
sudo du -sh /var/lib/machines/arena-test
```

A valid reuse requires all of the following:

1. `/var/lib/machines/arena-test` already exists and is the expected Debian 13 root filesystem.
2. The machine is not already running under another invocation.
3. The host has sufficient currently available resources for the specific authorized test.
4. The container can be started with private networking and its identity/root directory verified through `machinectl status`.

If the image is missing, corrupt, or cannot be verified, stop and report `NOT_VERIFIED` / `SKIPPED_BLOCKED`. Do not create a replacement, install LXC, use Docker, or fall back to the host.

## Start the preserved image

The image has no required `/etc/systemd/nspawn/arena-test.nspawn` configuration file. Start it explicitly with the existing root filesystem and private network, under a transient host systemd unit:

```sh
sudo systemd-run --unit=arena-test-container.service --collect --property=Restart=no /usr/bin/systemd-nspawn --directory=/var/lib/machines/arena-test --machine=arena-test --private-network --boot
```

Do not add host bind mounts, credentials, production data, model directories, or network interfaces. Do not enable the transient unit at boot or turn it into a persistent always-on service.

Verify the actual machine and root directory after startup:

```sh
machinectl status arena-test --no-pager
machinectl show arena-test -p Leader -p State -p RootDirectory -p ControlGroup --no-pager
```

The expected root directory is `/var/lib/machines/arena-test`; the machine class should be `container`, service `systemd-nspawn`, and the OS Debian 13. A successful `systemd-run` response alone is not sufficient verification.

## Resource and execution boundary

Starting the container only restores the test environment. It does **not** authorize model downloads, runtime installation, model execution, inference, or legal-move tests.

Before any model operation, perform a fresh host and container resource preflight and define explicit per-test memory, CPU, task, disk, and time limits. Do not infer safe limits from the image size or from the container's idle footprint. The transient container service currently has no dedicated `MemoryMax` or `CPUQuota`; model execution must remain blocked until a reviewed limit profile is supplied and verified. Never allow a model test to consume the host's production reserve.

All model artifacts, runtime files, temporary data, logs, and test outputs must remain inside this existing disposable container. Do not mount `/home/debian/ai-agent/models`, production paths, credentials, or the production database. Pin and verify each exact artifact's size and SHA-256 inside the container before loading it. The protected host model directory remains untouched.

## Stop and preserve the image

After an authorized test, stop only this named test machine and verify it is powered off. The root filesystem must be retained for later reuse:

```sh
sudo machinectl poweroff arena-test
machinectl list --all --no-pager
sudo test -d /var/lib/machines/arena-test
```

Do not delete `/var/lib/machines/arena-test`. Do not use `machinectl remove`, `rm -rf`, image export/import, or rebuild operations as routine cleanup. Remove only explicitly identified disposable test data inside the container, after confirming its exact path and ownership.

## Status vocabulary

- `PASS`: the exact pinned model artifact actually started inside the verified `arena-test` container and passed the required legal-move checks.
- `FAIL`: an explicitly authorized model attempt inside the verified container demonstrated a runtime failure.
- `SKIPPED_BLOCKED`: a safe preflight showed that the test cannot fit within the reviewed resource budget; do not start the model.
- `NOT_VERIFIED`: the existing image, isolation, resource envelope, or required evidence could not be verified. This is not a model failure.

A successful container boot or a worker-only recovery drill is not a model `PASS`.
