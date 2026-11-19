#!/usr/bin/env python3
"""Narrow HTTPS CONNECT proxy for the isolated arena-test machine.

No TLS interception: after validating the CONNECT target, bytes are relayed
unchanged. Intended to bind only to a dedicated host-side veth address.
"""
from __future__ import annotations

import argparse
import asyncio
import ipaddress
import logging
import re
import socket
from dataclasses import dataclass

# Keep exact hostnames. Add a destination only after observing a required
# redirect from the approved public Hugging Face download flow.
DEFAULT_ALLOWED_HOSTS = frozenset({
    "huggingface.co",
    "cas-server.xethub.hf.co",
    "cas-server.xethub-eu.hf.co",
    "transfer.xethub.hf.co",
    "transfer.xethub-eu.hf.co",
    "us.aws.cdn.hf.co",
    "us.gcp.cdn.hf.co",
    "cdn-lfs-us-1.hf.co",
    "cdn-lfs-eu-1.hf.co",
})
_HOST_RE = re.compile(r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)(?:\.(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?))*$", re.I)
_MAX_HEADER = 8192
_CONNECT_TIMEOUT = 15
_RELAY_IDLE_TIMEOUT = 120


class ProxyRequestError(ValueError):
    pass


@dataclass(frozen=True)
class ConnectTarget:
    hostname: str
    port: int


def parse_connect_request(header: bytes, allowed_hosts: frozenset[str] = DEFAULT_ALLOWED_HOSTS) -> ConnectTarget:
    if len(header) > _MAX_HEADER or not header.endswith(b"\r\n\r\n"):
        raise ProxyRequestError("invalid or oversized request header")
    try:
        lines = header.decode("ascii").split("\r\n")
    except UnicodeDecodeError as exc:
        raise ProxyRequestError("request must be ASCII") from exc
    parts = lines[0].split(" ")
    if len(parts) != 3 or parts[0] != "CONNECT" or parts[2] not in {"HTTP/1.0", "HTTP/1.1"}:
        raise ProxyRequestError("only HTTP CONNECT is supported")
    authority = parts[1]
    if "@" in authority or "/" in authority or "\\" in authority or "?" in authority or "#" in authority:
        raise ProxyRequestError("invalid CONNECT authority")
    if authority.startswith("[") or authority.count(":") != 1:
        raise ProxyRequestError("IP literals and malformed authorities are not allowed")
    hostname, port_text = authority.rsplit(":", 1)
    try:
        port = int(port_text, 10)
    except ValueError as exc:
        raise ProxyRequestError("invalid port") from exc
    if port != 443:
        raise ProxyRequestError("only TCP port 443 is allowed")
    try:
        ascii_host = hostname.rstrip(".").encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise ProxyRequestError("invalid hostname") from exc
    if not _HOST_RE.fullmatch(ascii_host) or ascii_host not in allowed_hosts:
        raise ProxyRequestError("destination hostname is not allowlisted")
    # Do not accept additional headers that could smuggle credentials or
    # influence proxy routing. Host is optional for CONNECT; if present it
    # must exactly match the authority hostname.
    for line in lines[1:]:
        if not line:
            continue
        if ":" not in line:
            raise ProxyRequestError("malformed header")
        name, value = line.split(":", 1)
        if name.strip().lower() == "host" and value.strip().lower() not in {ascii_host, f"{ascii_host}:443"}:
            raise ProxyRequestError("Host header does not match CONNECT authority")
        if name.strip().lower() not in {"host", "proxy-connection", "user-agent"}:
            raise ProxyRequestError("unsupported request header")
    return ConnectTarget(ascii_host, port)


def validate_public_addresses(addresses: list[str]) -> list[str]:
    unique = sorted(set(addresses))
    if not unique:
        raise ProxyRequestError("destination did not resolve")
    try:
        parsed = [ipaddress.ip_address(address) for address in unique]
    except ValueError as exc:
        raise ProxyRequestError("DNS returned an invalid address") from exc
    # Fail closed if DNS returns even one non-global address; this also
    # prevents mixed public/private answers from being partially accepted.
    if any(not address.is_global for address in parsed):
        raise ProxyRequestError("DNS returned a non-public destination address")
    return unique


async def resolve_public(hostname: str) -> list[str]:
    loop = asyncio.get_running_loop()
    records = await loop.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
    return validate_public_addresses([record[4][0] for record in records])


async def pipe_bidirectional(client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter,
                             upstream_reader: asyncio.StreamReader, upstream_writer: asyncio.StreamWriter) -> None:
    async def pump(source: asyncio.StreamReader, destination: asyncio.StreamWriter) -> None:
        try:
            while True:
                data = await asyncio.wait_for(source.read(65536), timeout=_RELAY_IDLE_TIMEOUT)
                if not data:
                    break
                destination.write(data)
                await destination.drain()
        except (asyncio.TimeoutError, ConnectionError, OSError):
            pass
        finally:
            try:
                destination.write_eof()
            except (AttributeError, ConnectionError, OSError, RuntimeError):
                pass

    tasks = [asyncio.create_task(pump(client_reader, upstream_writer)),
             asyncio.create_task(pump(upstream_reader, client_writer))]
    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                        allowed_hosts: frozenset[str], expected_client: str) -> None:
    peer = writer.get_extra_info("peername")
    peer_ip = peer[0] if peer else "unknown"
    upstream_writer = None
    hostname = "-"
    try:
        if peer_ip != expected_client:
            raise ProxyRequestError("client address is not authorized")
        header = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=5)
        target = parse_connect_request(header, allowed_hosts)
        hostname = target.hostname
        addresses = await resolve_public(target.hostname)
        last_error = None
        upstream_reader = None
        for address in addresses:
            try:
                upstream_reader, upstream_writer = await asyncio.wait_for(
                    asyncio.open_connection(address, target.port), timeout=_CONNECT_TIMEOUT)
                break
            except (OSError, asyncio.TimeoutError) as exc:
                last_error = exc
        if upstream_writer is None or upstream_reader is None:
            raise OSError(f"all resolved endpoints failed: {last_error}")
        writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await writer.drain()
        logging.info("allow client=%s host=%s ips=%s", peer_ip, hostname, ",".join(addresses))
        await pipe_bidirectional(reader, writer, upstream_reader, upstream_writer)
    except (ProxyRequestError, asyncio.IncompleteReadError, asyncio.LimitOverrunError,
            asyncio.TimeoutError, OSError, UnicodeError) as exc:
        logging.warning("deny client=%s host=%s reason=%s", peer_ip, hostname, str(exc)[:180])
        try:
            writer.write(b"HTTP/1.1 403 Forbidden\r\nConnection: close\r\nContent-Length: 0\r\n\r\n")
            await writer.drain()
        except (ConnectionError, OSError):
            pass
    finally:
        if upstream_writer is not None:
            upstream_writer.close()
            try:
                await upstream_writer.wait_closed()
            except OSError:
                pass
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass


async def main_async(args: argparse.Namespace) -> None:
    configured_hosts = args.allow_host if args.allow_host is not None else DEFAULT_ALLOWED_HOSTS
    allowed = frozenset(host.strip().lower() for host in configured_hosts if host.strip())
    if not allowed or any(not _HOST_RE.fullmatch(host) for host in allowed):
        raise SystemExit("invalid or empty hostname allowlist")
    try:
        ipaddress.ip_address(args.listen)
        ipaddress.ip_address(args.client)
    except ValueError as exc:
        raise SystemExit("listen and client must be literal IP addresses") from exc
    connection_slots = asyncio.Semaphore(4)

    async def accept_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        async with connection_slots:
            await handle_client(reader, writer, allowed, args.client)

    server = await asyncio.start_server(
        accept_client, host=args.listen, port=args.port,
        limit=_MAX_HEADER + 1, backlog=16)
    logging.info("listening address=%s port=%d expected_client=%s allowlist=%s",
                 args.listen, args.port, args.client, ",".join(sorted(allowed)))
    async with server:
        await server.serve_forever()



def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--listen", required=True, help="dedicated host-side veth address")
    parser.add_argument("--client", required=True, help="exact container-side source address")
    parser.add_argument("--port", type=int, default=18765)
    parser.add_argument("--allow-host", action="append", default=None,
                        help="exact allowed hostname; repeat to override built-in list")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("port must be an unprivileged TCP port")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        asyncio.run(main_async(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
