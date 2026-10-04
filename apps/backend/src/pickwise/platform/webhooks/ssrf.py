# SPDX-License-Identifier: AGPL-3.0-only
"""Keeping webhooks from being a way into our network (ADR 0017).

A tenant admin chooses the URL we call, so the URL is untrusted input. We require https,
resolve the host and refuse any target that has a non-public address (loopback,
private, link-local, cloud metadata, carrier-grade NAT, multicast, …). The check runs at
registration and again before every delivery, and the delivery then connects to the
address that was checked rather than resolving again, so a DNS answer that changes
between the check and the connection (rebinding) can't redirect it. If a name has several
addresses, all of them must be public. Redirects are never followed.

``WEBHOOK_ALLOW_PRIVATE_TARGETS`` (refused in production) turns the address check off
and allows plain http, for receivers on a development network.
"""

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

MAX_URL_LENGTH = 2048

Resolver = Callable[[str, int], Awaitable[list[str]]]

_EXTRA_BLOCKED = tuple(
    ipaddress.ip_network(n)
    for n in (
        "64:ff9b::/96",  # NAT64: embeds an arbitrary IPv4 address
        "64:ff9b:1::/48",
        "2002::/16",  # 6to4
        "2001::/32",  # Teredo
        "100.64.0.0/10",  # carrier-grade NAT (also Alibaba's metadata at 100.100.100.200)
    )
)


class UnsafeTargetError(ValueError):
    """The URL isn't an acceptable webhook target. The message is safe to show the user."""


@dataclass(frozen=True, slots=True)
class Target:
    url: str
    scheme: str
    host: str
    port: int
    # Every address the host resolved to; all passed the check.
    addresses: tuple[str, ...]

    def pinned_url(self) -> str:
        """The URL with the host replaced by a checked address (send ``Host: <host>``)."""
        parts = urlsplit(self.url)
        address = self.addresses[0]
        host = f"[{address}]" if ":" in address else address
        return urlunsplit((parts.scheme, f"{host}:{self.port}", parts.path or "/", parts.query, ""))


def is_blocked(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if (
        not ip.is_global
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_unspecified
    ):
        return True
    return any(ip in net for net in _EXTRA_BLOCKED if ip.version == net.version)


async def system_resolver(host: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise UnsafeTargetError("The host name doesn't resolve.") from exc
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


def parse(url: str, *, allow_private: bool) -> tuple[str, str, int]:
    """Syntax rules: (scheme, host, port). Raises ``UnsafeTargetError``."""
    if len(url) > MAX_URL_LENGTH:
        raise UnsafeTargetError("The URL is too long.")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise UnsafeTargetError("That isn't a valid URL.") from exc
    scheme = parts.scheme.lower()
    allowed = ("https", "http") if allow_private else ("https",)
    if scheme not in allowed:
        raise UnsafeTargetError("Webhook URLs must start with https://.")
    if parts.username is not None or parts.password is not None:
        raise UnsafeTargetError("Put credentials in the signature check, not the URL.")
    if parts.fragment:
        raise UnsafeTargetError("The URL can't have a #fragment.")
    host = (parts.hostname or "").rstrip(".").lower()
    if not host:
        raise UnsafeTargetError("The URL needs a host name.")
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise UnsafeTargetError("That host name isn't valid.") from exc
    return scheme, host, port or (443 if scheme == "https" else 80)


async def resolve_target(
    url: str, *, allow_private: bool, resolver: Resolver = system_resolver
) -> Target:
    """Parse and resolve ``url``; refuse it unless every address is public."""
    scheme, host, port = parse(url, allow_private=allow_private)
    try:
        ipaddress.ip_address(host)
        addresses = [host]
    except ValueError:
        addresses = await resolver(host, port)
    if not addresses:
        raise UnsafeTargetError("The host name doesn't resolve.")
    if not allow_private and any(is_blocked(a) for a in addresses):
        raise UnsafeTargetError("That address isn't reachable from the internet.")
    return Target(url, scheme, host, port, tuple(addresses))
