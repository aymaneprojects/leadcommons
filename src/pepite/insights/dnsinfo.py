"""MX and TXT records of a domain (public DNS), used to spot email providers and tools."""

from __future__ import annotations

import logging

import dns.exception
import dns.resolver

log = logging.getLogger(__name__)


def lookup(domain: str, *, lifetime: float = 4.0) -> dict[str, list[str]]:
    """{"mx": [host, ...], "txt": [string, ...]}; empty lists when DNS is unreachable."""
    out: dict[str, list[str]] = {"mx": [], "txt": []}
    resolver = dns.resolver.Resolver()
    resolver.lifetime = lifetime
    resolver.timeout = lifetime
    try:
        for r in resolver.resolve(domain, "MX"):
            out["mx"].append(str(r.exchange).lower())
    except (dns.exception.DNSException, OSError) as exc:
        log.debug("MX lookup for %s failed: %s", domain, exc)
    try:
        for r in resolver.resolve(domain, "TXT"):
            out["txt"].append(b"".join(r.strings).decode("utf-8", "replace"))
    except (dns.exception.DNSException, OSError) as exc:
        log.debug("TXT lookup for %s failed: %s", domain, exc)
    return out
