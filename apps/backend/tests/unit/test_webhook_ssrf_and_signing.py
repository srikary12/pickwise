# SPDX-License-Identifier: AGPL-3.0-only
"""The webhook target policy and the signature scheme."""

import pytest

from pickwise.platform.webhooks.signing import sign, verify
from pickwise.platform.webhooks.ssrf import (
    Resolver,
    UnsafeTargetError,
    is_blocked,
    parse,
    resolve_target,
)

BLOCKED = [
    "127.0.0.1",
    "127.1.2.3",
    "10.0.0.5",
    "172.16.0.1",
    "172.31.255.255",
    "192.168.1.1",
    "169.254.169.254",  # cloud metadata
    "100.100.100.200",  # carrier-grade NAT, Alibaba metadata
    "0.0.0.0",  # noqa: S104 - an address under test, not a bind
    "224.0.0.1",
    "255.255.255.255",
    "::1",
    "fe80::1",
    "fd00:ec2::254",  # AWS IPv6 metadata
    "fc00::1",
    "::ffff:127.0.0.1",  # IPv4-mapped loopback
    "::ffff:10.0.0.1",
    "64:ff9b::7f00:1",  # NAT64 of 127.0.0.1
    "2002:7f00:1::",  # 6to4 of 127.0.0.1
]
PUBLIC = ["93.184.216.34", "8.8.8.8", "2606:4700:4700::1111"]


@pytest.mark.parametrize("address", BLOCKED)
def test_non_public_addresses_are_blocked(address: str) -> None:
    assert is_blocked(address)


@pytest.mark.parametrize("address", PUBLIC)
def test_public_addresses_are_allowed(address: str) -> None:
    assert not is_blocked(address)


@pytest.mark.parametrize(
    "url",
    [
        "http://hooks.example.com/x",  # not https
        "ftp://hooks.example.com/x",
        "https://user:pw@hooks.example.com/x",  # credentials in the URL
        "https://hooks.example.com/x#frag",
        "https:///nohost",
        "https://hooks.example.com:notaport/x",
        "https://" + "a" * 2100 + ".example.com/",
        "javascript:alert(1)",
    ],
)
def test_bad_urls_are_refused(url: str) -> None:
    with pytest.raises(UnsafeTargetError):
        parse(url, allow_private=False)


def test_http_is_allowed_only_with_the_dev_flag() -> None:
    assert parse("http://receiver:9000/hook", allow_private=True) == ("http", "receiver", 9000)
    assert parse("https://Hooks.Example.com./x", allow_private=False) == (
        "https",
        "hooks.example.com",
        443,
    )


def resolver_for(*addresses: str) -> Resolver:
    async def resolve(host: str, port: int) -> list[str]:
        return list(addresses)

    return resolve


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1/x",
        "https://[::1]/x",
        "https://169.254.169.254/latest/meta-data",
        "https://2130706433/x",  # decimal form of 127.0.0.1, resolved by the system resolver
    ],
)
async def test_literal_private_hosts_are_refused(url: str) -> None:
    async def resolve(host: str, port: int) -> list[str]:
        return ["127.0.0.1"]  # what getaddrinfo says for the decimal form

    with pytest.raises(UnsafeTargetError):
        await resolve_target(url, allow_private=False, resolver=resolve)


async def test_a_name_resolving_to_a_private_address_is_refused() -> None:
    resolve = resolver_for("10.0.0.7")
    with pytest.raises(UnsafeTargetError):
        await resolve_target("https://hooks.example.com/x", allow_private=False, resolver=resolve)


async def test_every_address_must_be_public() -> None:
    resolve = resolver_for("93.184.216.34", "127.0.0.1")
    with pytest.raises(UnsafeTargetError):
        await resolve_target("https://hooks.example.com/x", allow_private=False, resolver=resolve)


async def test_a_public_name_is_pinned_to_its_address() -> None:
    resolve = resolver_for("93.184.216.34")
    target = await resolve_target(
        "https://hooks.example.com/a/b?x=1", allow_private=False, resolver=resolve
    )
    assert target.pinned_url() == "https://93.184.216.34:443/a/b?x=1"
    v6 = await resolve_target(
        "https://hooks.example.com/",
        allow_private=False,
        resolver=resolver_for("2606:4700:4700::1111"),
    )
    assert v6.pinned_url() == "https://[2606:4700:4700::1111]:443/"


async def test_the_dev_flag_allows_private_targets() -> None:
    resolve = resolver_for("172.18.0.9")
    target = await resolve_target("http://web:8089/hook", allow_private=True, resolver=resolve)
    assert target.pinned_url() == "http://172.18.0.9:8089/hook"


def test_signature_round_trip_and_tampering() -> None:
    body = b'{"id":"1"}'
    header = sign("whsec_x", body, 1_700_000_000)
    assert header.startswith("t=1700000000,v1=")
    assert verify("whsec_x", body, header, now=1_700_000_100)
    assert not verify("whsec_y", body, header, now=1_700_000_100)  # wrong secret
    assert not verify("whsec_x", body + b" ", header, now=1_700_000_100)  # altered body
    assert not verify("whsec_x", body, header, now=1_700_000_000 + 301)  # too old
    assert not verify("whsec_x", body, header, now=1_700_000_000 - 301)  # from the future
    for malformed in ("", "v1=abc", "t=abc,v1=abc", "garbage"):
        assert not verify("whsec_x", body, malformed, now=1_700_000_000)
