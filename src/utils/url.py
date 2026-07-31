from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

TRACKING_KEYS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "ref",
    "source",
}
BLOCKED_HOSTS = {
    "localhost",
    "localhost.localdomain",
    "metadata.google.internal",
}


class UnsafeUrlError(ValueError):
    pass


def _is_public_ip(value: str) -> bool:
    address = ipaddress.ip_address(value)
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def normalize_url(url: str) -> str:
    parsed = urlsplit(url.strip())
    if parsed.scheme.lower() not in {"http", "https"}:
        raise UnsafeUrlError("only HTTP and HTTPS URLs are allowed")
    if parsed.username or parsed.password:
        raise UnsafeUrlError("URL credentials are not allowed")
    if not parsed.hostname:
        raise UnsafeUrlError("URL host is required")
    host = parsed.hostname.rstrip(".").lower().encode("idna").decode("ascii")
    if host in BLOCKED_HOSTS or host.endswith(".localhost"):
        raise UnsafeUrlError("local hosts are blocked")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and not _is_public_ip(host):
        raise UnsafeUrlError("non-public IP address is blocked")
    try:
        port = parsed.port
    except ValueError as exc:
        raise UnsafeUrlError("invalid port") from exc
    default_port = (parsed.scheme.lower() == "http" and port == 80) or (
        parsed.scheme.lower() == "https" and port == 443
    )
    host_for_netloc = f"[{host}]" if address and address.version == 6 else host
    netloc = (
        host_for_netloc
        if port is None or default_port
        else f"{host_for_netloc}:{port}"
    )
    clean_query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in TRACKING_KEYS
    ]
    path = parsed.path or "/"
    return urlunsplit(
        (parsed.scheme.lower(), netloc, path, urlencode(sorted(clean_query)), "")
    )


async def ensure_public_url(
    url: str,
    resolver: Callable[..., Awaitable[list[tuple[object, ...]]]] | None = None,
) -> str:
    normalized = normalize_url(url)
    hostname = urlsplit(normalized).hostname
    if hostname is None:
        raise UnsafeUrlError("URL host is required")
    try:
        ipaddress.ip_address(hostname)
        return normalized
    except ValueError:
        pass

    if resolver is None:

        async def system_resolver(host: str, port: int) -> list[tuple[object, ...]]:
            return await asyncio.to_thread(
                socket.getaddrinfo, host, port, type=socket.SOCK_STREAM
            )

        resolver = system_resolver

    parsed = urlsplit(normalized)
    default_port = 443 if parsed.scheme == "https" else 80
    records = await resolver(hostname, parsed.port or default_port)
    if not records:
        raise UnsafeUrlError("host did not resolve")
    for record in records:
        address = str(record[4][0])  # type: ignore[index]
        if not _is_public_ip(address):
            raise UnsafeUrlError("host resolves to a non-public IP address")
    return normalized


def resolve_redirect(current_url: str, location: str) -> str:
    return normalize_url(urljoin(current_url, location))
