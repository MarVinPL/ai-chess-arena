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

The Hugging Face portion of the built-in allowlist is intentionally limited to two exact DNS names:

- `huggingface.co` — pinned-revision resolve endpoint
- `us.aws.cdn.hf.co` — regional Xet CDN destination observed in the signed redirect

Evidence collected from the VPS on 2026-11-19: HTTPS HEAD requests to the pinned artifact URLs for candidates 1–4 returned `302` from `huggingface.co` with a redirect to `us.aws.cdn.hf.co`, followed by `200`. No model bytes were downloaded. Candidate 5's supplied pinned URL returned `404`; its repository/revision/file tuple must be reconciled separately before that candidate can be downloaded or tested. The two-host allowlist therefore reflects the observed successful redirect chain, not the complete set of destinations that Hugging Face may use in other regions or for other repositories.

The package-install allowlist is separately limited to two exact PyPI hostnames:

- `pypi.org` — package index and project metadata over HTTPS
- `files.pythonhosted.org` — package distribution files served by PyPI

These are included only to support a separately approved installation of a CPU-compatible Python GGUF runtime inside `arena-test`. The proxy tunnels TLS without interception and cannot inspect package paths, so this is a hostname-level—not package-name-level—restriction. `upload.pypi.org`, `pypi.python.org`, arbitrary mirrors, GitHub, and all other hosts remain blocked. The PyPI upload hostname is intentionally excluded. Use pip with `--index-url https://pypi.org/simple` and `--only-binary=:all:` for the proposed wheel-only installation; do not enable source builds or extra indexes. This PR does not install anything or authorize a package download. Before an installation window, verify the exact package/version/wheel compatibility and hashes, then enable the proxy only for that separately authorized operation.

The official machine-readable endpoint list at `https://huggingface.co/.well-known/meta.json` contains additional CDN, Xet, and cloud-storage domains. They are deliberately **not** allowlisted merely because they appear in that list. Do not add `amazonaws.com`, `s3.*`, generic cloud domains, upload endpoints, or unrelated regional endpoints. If a future approved download redirects elsewhere, stop and record the exact pinned URL, redirect hostname, and successful metadata probe before proposing a narrowly scoped allowlist change.

### DNS and HTTPS endpoint audit (VPS, 2026-11-19)

Fetched the official `meta.json` endpoint arrays (`hubDomains`, `lfsDomains`, `cdnDomains`) and tested all 23 unique hostnames from the VPS. All 23 resolved successfully; every returned A/AAAA address was classified as globally routable. A TLS-verified HTTPS `HEAD /` probe also reached every hostname:

- `hubDomains` (4): all returned HTTP 200.
- `cdnDomains` (12): all returned HTTP 200.
- `lfsDomains` (7): the five `hf.co` LFS/Xet hosts returned HTTP 403 at `/`, and the two regional S3 hosts returned HTTP 405 to `HEAD /`. These are application-level responses from reachable HTTPS endpoints, not DNS or TLS failures; probing `/` does not establish authorization or suitability for an artifact transfer.

This is a DNS/TLS reachability inventory only. It does **not** prove that all 23 hosts are required for the five pinned files, nor does it justify widening the proxy allowlist. Only the observed `huggingface.co` → `us.aws.cdn.hf.co` redirect chain for candidates 1–4 is currently evidenced as necessary. The audit used metadata and HEAD requests only; no model data was downloaded.

Official reference: [Hugging Face — Downloading models, “Downloading behind a proxy or firewall”](https://huggingface.co/docs/hub/en/models-downloading).

## PyPI runtime-install destinations

The additional PyPI entries are exactly `pypi.org` and `files.pythonhosted.org`; they do not include upload endpoints or general package mirrors. They are intended for an explicitly authorized, wheel-only `pip` operation. No `pip install`, package download, runtime installation, or inference is performed by this PR.

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

## Implementation files in this PR

- scripts/arena_test_egress_proxy.py: standard-library-only CONNECT proxy. It accepts only exact allowlisted DNS names on port 443 (the two evidenced Hugging Face hosts and two PyPI package hosts), resolves names on the host, rejects any non-global result, pins the upstream TCP connection to the validated IP, and relays TLS bytes without interception.
- systemd/arena-test-egress-proxy.service: unprivileged, hardened service bound to the dedicated host veth address.
- systemd/10-arena-test-egress.network: static address for only the dedicated host veth.
- systemd/arena-test.nspawn: adds a second, named veth while retaining private networking.
- scripts/arena_test_egress_firewall.sh: narrowly scoped IPv4/IPv6 INPUT and FORWARD chains for that interface; refuses pre-existing chain names and removes only its own rules.
- tests/test_arena_test_egress_proxy.py: standard-library unit tests; no network access required.

## Deployment sequence (not performed by this PR)

Deployment is a separate owner-approved operation. It requires a maintenance window because adding the second veth requires restarting the existing arena-test machine.

1. Confirm the exact machine/rootfs, current service command, current resource limits, and clean firewall-chain names.
2. Install the proxy script as /usr/local/libexec/arena-test-egress-proxy.py (root:root, mode 0755), the service as /etc/systemd/system/arena-test-egress-proxy.service, the networkd file as /etc/systemd/network/10-arena-test-egress.network, and the nspawn file as /etc/systemd/nspawn/arena-test.nspawn. Install the documentation under /usr/local/share/doc/arena-test/.
3. Apply `scripts/arena_test_egress_firewall.sh apply` **before creating the veth**. The script accepts an absent `at-egress` interface and installs interface-name-specific IPv4/IPv6 INPUT and FORWARD rules ahead of time; if the interface already exists, it validates the expected host address. This prevents an unfiltered startup window. Inspect the exact chains and jumps; the script self-rolls back partial setup on failure.
4. Reload systemd and systemd-networkd configuration. Stop only arena-test-container.service, then start the same existing rootfs using the trusted `/etc/systemd/nspawn/arena-test.nspawn` settings. **Do not pass `--private-network` on the command line**: on the deployed Debian 13 / systemd 257.13 host, that explicit flag prevented `VirtualEthernetExtra=at-egress:eth1` in the settings file from being applied. Let the settings file provide `Private=yes` and the extra veth. The verified start command is `/usr/bin/systemd-nspawn --directory=/var/lib/machines/arena-test --machine=arena-test --settings=trusted --boot`. Do not recreate the machine or rootfs.
5. Verify the host interface has only 10.203.181.1/30 and container eth1 has only 10.203.181.2/30. Verify `net.ipv4.conf.at-egress.forwarding=0` and `net.ipv6.conf.at-egress.forwarding=0`, only the connected IPv4 /30 route exists in the container, no IPv4/IPv6 default route exists, and no usable DNS server is configured. Do not add a default route or DNS server.
6. Start the proxy service only for the non-model acceptance test or a separately authorized download window. Confirm it listens only on 10.203.181.1:18765 and runs under a transient DynamicUser identity.
7. From inside the container, use the proxy explicitly for HTTPS requests (for example, set HTTPS_PROXY=http://10.203.181.1:18765 and HTTP_PROXY=http://10.203.181.1:18765 for the one process only). Do not persist proxy variables globally. The client should not need DNS because the proxy resolves the destination.
8. Test one small, non-model HTTPS metadata request first. Verify an approved hostname succeeds and a non-allowlisted hostname, IP literal, alternate port, private address, direct DNS (UDP and TCP), IPv4/IPv6 direct Internet connection, and host management ports fail. Confirm the container cannot reach other host services.
9. After acceptance tests, stop the proxy and keep the firewall chains active. Keep the proxy stopped until a download window is separately authorized. Only during an approved window may the proxy be started. Only after all isolation checks pass and a separate download authorization is given may the approved pinned model artifacts be downloaded. This issue itself does not authorize that download.
10. When the download window ends, stop the proxy first. If the second veth is to be retained, keep the interface-specific firewall chains in place so all host access except the stopped proxy port remains blocked. Remove the firewall chains only as part of full teardown together with the extra veth (remove the nspawn/networkd/service files and restart the same container), then verify the host `at-egress` and container `eth1` interfaces are gone. Keep the second veth only if the owner explicitly wants the facility retained.

Do not run steps 2–10 as part of review or CI. Any uncertainty, unexpected interface/address, non-empty pre-existing chain, or failed isolation test is a hard stop.
