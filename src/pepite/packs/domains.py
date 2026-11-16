"""Domain normalisation: URL or host to a bare registrable domain."""

from __future__ import annotations

from urllib.parse import urlsplit

# Common second-level public suffixes (small table; avoids a PSL dependency).
TWO_LEVEL_SUFFIXES: frozenset[str] = frozenset(
    {
        "co.uk", "org.uk", "ac.uk", "gov.uk", "ltd.uk", "plc.uk", "me.uk",
        "com.au", "net.au", "org.au", "edu.au", "gov.au",
        "co.jp", "or.jp", "ne.jp", "ac.jp", "go.jp",
        "com.br", "net.br", "org.br", "gov.br",
        "co.za", "org.za", "net.za", "gov.za",
        "co.nz", "org.nz", "net.nz",
        "co.in", "net.in", "org.in", "firm.in",
        "com.mx", "com.ar", "com.co", "com.tr", "com.cn", "com.hk", "com.sg", "com.my",
        "com.tw", "com.pl", "com.ua", "com.eg", "com.sa", "com.ng", "com.pk", "com.vn",
        "co.kr", "co.id", "co.il", "co.ke", "co.th",
        "co.ma", "com.ma", "org.ma", "gov.ma",
        "com.tn", "com.dz",
    }
)  # fmt: skip


def normalize_domain(value: str | None) -> str | None:
    """Return lowercase registrable domain for a URL/host, or None if not usable."""
    if not value:
        return None
    raw = value.strip().lower()
    if not raw:
        return None
    if "://" not in raw:
        raw = "//" + raw
    try:
        host = urlsplit(raw).hostname
    except ValueError:
        return None
    if not host:
        return None
    host = host.rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    labels = [p for p in host.split(".") if p]
    if len(labels) < 2 or not all(
        p.replace("-", "").isalnum() or p.startswith("xn--") for p in labels
    ):
        return None
    if ".".join(labels[-2:]) in TWO_LEVEL_SUFFIXES and len(labels) >= 3:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])
