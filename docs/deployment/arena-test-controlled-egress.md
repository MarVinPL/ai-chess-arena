# Controlled outbound access for arena-test

## Status

Design proposal for Issue #181. This document does not enable networking, change the running machine, install packages, download artifacts, or authorize model execution. Apply only after this PR is reviewed and the owner separately authorizes deployment.

## Goal and boundary

Keep the existing Debian 13 `systemd-nspawn` machine at `/var/lib/machines/arena-test`. Replace the current all-or-nothing network block with a temporary, narrowly scoped outbound path for anonymous HTTPS downloads from Hugging Face.

The container must not receive:
- host networking or a general-purpose default route;
- direct DNS or arbitrary outbound connectivity;
- inbound connections from the Internet or other containers;
- access to production services, databases, credentials, application directories, or host model directories.

No Docker, LXC, replacement VM, host-side model execution, or transfer of historical host-side model files is permitted.

## Proposed network shape

Use a dedicated host-side HTTPS CONNECT proxy reachable only over a private veth link created for `arena-test`.

- Bind the proxy only to the host end of the dedicated veth; never to the public interface.
- Permit the container to reach only the proxy listener and only for the duration of the approved artifact download window.
- The proxy accepts CONNECT requests only, on TCP 443, and validates the complete destination hostname against an explicit allowlist.
- Resolve names on the host side; reject loopback, link-local, private, multicast, reserved, and otherwise non-public destination addresses after resolution. Connect to the validated resolved address to avoid a second untrusted DNS lookup.
- Reject IP-literal destinations, user-info, malformed hostnames, alternate ports, and non-allowlisted names. Do not support arbitrary HTTP forwarding.
- Do not enable IP forwarding/NAT for the container. Do not create a general route from the container to the host or Internet.
- Log timestamp, source, requested hostname, decision, and byte counts; do not log authorization headers, query strings, or file contents.
- Remove the temporary veth, proxy process/unit, and firewall rules after the download window. Verify the container has no remaining route or listener access.

The implementation must use a dedicated, auditable unit/script and uniquely named firewall rules/chains so rollback removes only resources created for this purpose. Never flush global firewall tables or change unrelated host networking.

## Hugging Face download destinations

The current official Hub documentation lists these HTTPS destinations for model downloads:

- `huggingface.co` — Hub API, metadata, and redirects
- `cas-server.xethub.hf.co`
- `cas-server.xethub-eu.hf.co`
- `transfer.xethub.hf.co`
- `transfer.xethub-eu.hf.co`
- `us.aws.cdn.hf.co`
- `us.gcp.cdn.hf.co`
- `cdn-lfs-us-1.hf.co`
- `cdn-lfs-eu-1.hf.co`

Treat this as a starting allowlist, not an immutable guarantee. Before enabling egress, inspect the current machine-readable endpoint list at `https://huggingface.co/.well-known/meta.json` from the host and reconcile it with the official documentation. Add only download endpoints actually required by the selected client and the five approved public repositories. Do not add upload destinations or broad cloud-storage domains. If an additional destination is required, stop and document the evidence before changing the allowlist.

Official reference: [Hugging Face — Downloading models, “Downloading behind a proxy or firewall”](https://huggingface.co/docs/hub/en/models-downloading).

## Download controls

- Use only the five already approved repository/revision/file tuples. Do not search for or add candidates.
- Download directly inside `arena-test`; never download on the host and copy the weights into the container.
- Use anonymous, read-only downloads for public repositories; no Hugging Face token is needed or to be placed in the container.
- Verify exact byte size and SHA-256 inside the container before loading any artifact.
- Keep model weights, runtime, logs, and test output inside the existing container rootfs.
- Do not start model inference as part of this networking issue.

## Resource controls

Immediately before each separately authorized model test:
- read current host `MemAvailable`;
- set the transient service `MemoryMax` to 90% of that value;
- set `CPUQuota=100%`;
- verify the effective cgroup values and run the safety preflight;
- skip without attempting execution if the preflight detects risk to the host.

Do not assume transient cgroup limits survive a container restart.

## Acceptance tests before any model download

1. Confirm the machine identity and rootfs are still `arena-test` and `/var/lib/machines/arena-test`.
2. Confirm the proxy listens only on the dedicated veth address.
3. Confirm an HTTPS CONNECT to each required, currently approved Hugging Face hostname succeeds.
4. Confirm non-allowlisted domains, IP literals, non-443 ports, and private/link-local destinations are rejected.
5. Confirm the container cannot reach the host public address, production endpoints, arbitrary Internet IPs, or other private networks directly.
6. Confirm no inbound Internet path to the container exists.
7. Confirm logs contain decisions and byte counts but no credentials or URL query strings.
8. Remove the temporary path and verify all temporary network objects are gone.

A failed or inconclusive isolation test means STOP; do not download artifacts.

## Rollback

Stop the dedicated proxy unit, remove only the dedicated firewall rules/chains and veth configuration created by this feature, then verify:
- the container no longer reaches the proxy;
- the container has no direct Internet route;
- the host public interface, existing firewall policy, and unrelated services are unchanged.

Do not restart or recreate the rootfs as a rollback shortcut.

## Out of scope

This issue does not authorize artifact downloads, runtime installation, inference, tournament changes, allowlist/roster changes, production deployment, or changes to the production database. Those actions remain subject to their existing explicit approvals and preflight requirements.
