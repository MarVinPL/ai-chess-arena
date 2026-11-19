# Model execution isolation policy

Status: owner-mandated project rule (2026-11-19)

## Mandatory execution boundary

- All model artifact downloads, runtime installation, model loading, inference smoke tests, legal-move tests, and other execution of model artifacts MUST run inside the already-existing disposable `systemd-nspawn` machine `arena-test`.
- The confirmed machine image is the existing Debian 13 root filesystem at `/var/lib/machines/arena-test`. It is a `systemd-nspawn` container, **not LXC**.
- Never download model weights, install model runtimes, load model artifacts, or execute inference directly on the VPS host.
- Do not substitute the host, Docker, a newly created container/image, LXC, a VM, or another environment for this exact `arena-test` machine.
- Do not create, rebuild, rebootstrap, or recreate `arena-test` if its existing image is missing or cannot be verified. Stop and report the blocker; ask the owner before changing the isolation environment.
- Before a test, verify the machine identity and running state using the runbook in [`arena-test-container.md`](deployment/arena-test-container.md), inspect host and container cgroup resource limits and available resources, and confirm that the test cannot access production paths, services, databases, model directories, or credentials.
- Transfer only the exact pinned artifact required for the authorized test into the isolated container. Verify its expected byte size and SHA-256 inside the container before loading it.
- Keep downloaded weights, runtime binaries, temporary logs, and test outputs inside `arena-test`; do not copy them to host model directories or production locations.
- If the existing image is unavailable, its identity cannot be verified, or its resource budget is insufficient, do not fall back to host execution. Record `NOT_VERIFIED` or `SKIPPED_BLOCKED` as appropriate, with evidence.

## Existing host-side material

A runtime binary may already exist on the host for unrelated or historical reasons. Its presence is not permission to execute it on the host. Do not install, replace, or invoke it outside `arena-test`.

## Result semantics

- `PASS`: exact pinned artifact verified and actually started inside `arena-test`; legal-move checks completed as required.
- `FAIL`: an authorized test inside `arena-test` demonstrated a runtime or legal-move failure.
- `SKIPPED_BLOCKED`: safe resource/isolation preflight showed the test cannot be run within the existing container's available budget.
- `NOT_VERIFIED`: no valid isolated execution test has been completed. This is not a failure.

A technical `PASS` does not grant license clearance, admission, allowlist changes, roster changes, or tournament participation.
