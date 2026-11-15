"""Mail providers behind a domain (from its MX hosts), and what an SMTP probe can tell there.

`rcpt` says how far a RCPT TO answer can be trusted for that provider:

    reliable  a 250 for the address and a 550 for a random one means the mailbox exists
    limited   the provider answers RCPT, but acceptance proves less: big tenants hold many
              aliases, groups, shared and former-staff mailboxes, some setups (hybrid, split
              delivery) accept at RCPT and bounce later, and probing IPs get throttled or
              blocked quickly. Google Workspace and Microsoft 365 are here: an address they
              accept is never shown as valid on that answer alone.
    hidden    a filtering gateway in front of the real mailboxes; it usually accepts every
              recipient or rejects for policy reasons, so its answers say little

The table is an informed default, not a measurement: re-measure it with
`leadcommons eval emails --gold <real gold set> --smtp` from a host with port 25 open.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Provider:
    slug: str
    label: str
    rcpt: str  # reliable | limited | hidden
    min_interval: float  # seconds between two SMTP sessions to one of its MX hosts
    note: str


PROVIDERS: dict[str, Provider] = {
    p.slug: p
    for p in (
        Provider(
            "google",
            "Google Workspace",
            "limited",
            3.0,
            "Rejects unknown users at RCPT unless a catch-all route is set, but an accepted "
            "address can be an alias or group, and probing IPs are throttled.",
        ),
        Provider(
            "microsoft",
            "Microsoft 365",
            "limited",
            3.0,
            "Rejects unknown users only when directory-based edge blocking is on; hybrid "
            "tenants accept everything and bounce later; probing IPs are often blocked.",
        ),
        Provider("zoho", "Zoho Mail", "reliable", 2.0, "Rejects unknown users at RCPT."),
        Provider(
            "proton",
            "Proton Mail",
            "reliable",
            2.0,
            "Rejects unknown users unless the domain has a catch-all address.",
        ),
        Provider(
            "ovh",
            "OVHcloud",
            "reliable",
            2.0,
            "Answers RCPT for its hosted mailboxes; redirect-only domains accept everything.",
        ),
        Provider(
            "ionos",
            "IONOS",
            "reliable",
            2.0,
            "Answers RCPT; many small domains are catch-all, which the random-address test finds.",
        ),
        Provider(
            "gandi", "Gandi", "reliable", 2.0, "Rejects unknown users unless a wildcard is set."
        ),
        Provider("yahoo", "Yahoo", "limited", 3.0, "Consumer and small-business mail; throttles."),
        Provider("fastmail", "Fastmail", "reliable", 2.0, "Rejects unknown users at RCPT."),
        Provider("icloud", "iCloud Mail", "limited", 3.0, "Custom domains; throttles probing."),
        Provider("yandex", "Yandex 360", "reliable", 2.0, "Rejects unknown users at RCPT."),
        Provider("amazon", "Amazon WorkMail", "reliable", 2.0, "Rejects unknown users at RCPT."),
        Provider("rackspace", "Rackspace Email", "reliable", 2.0, "Rejects unknown users at RCPT."),
        Provider("godaddy", "GoDaddy", "limited", 2.0, "Often accepts at RCPT and bounces later."),
        Provider("strato", "Strato", "reliable", 2.0, "Answers RCPT; catch-all is common."),
        Provider("infomaniak", "Infomaniak", "reliable", 2.0, "Rejects unknown users at RCPT."),
        Provider("titan", "Titan / Hostinger", "reliable", 2.0, "Rejects unknown users at RCPT."),
        Provider(
            "gateway",
            "Email security gateway",
            "hidden",
            3.0,
            "A filtering service (Mimecast, Proofpoint, Barracuda, Cisco, ...) in front of the "
            "real mailboxes; it often accepts every recipient, so RCPT says little.",
        ),
        Provider(
            "self",
            "Own mail server",
            "reliable",
            1.0,
            "Usually rejects unknown users at RCPT; some greylist first-time senders.",
        ),
        Provider("other", "Other provider", "reliable", 1.0, "No specific knowledge."),
    )
}

# MX host suffix (or regex) -> provider slug, most specific first.
_MX_RULES: tuple[tuple[str, str], ...] = (
    (r"(^|\.)aspmx\.l\.google\.com$", "google"),
    (r"(^|\.)googlemail\.com$", "google"),
    (r"^smtp\.google\.com$", "google"),
    (r"\.mail\.protection\.outlook\.com$", "microsoft"),
    (r"\.mail\.protection\.partner\.outlook\.cn$", "microsoft"),
    (r"\.mx\.microsoft$", "microsoft"),
    (r"\.olc\.protection\.outlook\.com$", "microsoft"),
    (r"(^|\.)zoho(mail)?\.(com|eu|in|com\.au|jp|com\.cn|sa)$", "zoho"),
    (r"(^|\.)protonmail\.ch$", "proton"),
    (r"(^|\.)proton\.me$", "proton"),
    (r"(^|\.)ovh\.net$", "ovh"),
    (r"(^|\.)ovh\.(com|ca)$", "ovh"),
    (r"(^|\.)(ionos|1and1)\.[a-z.]+$", "ionos"),
    (r"(^|\.)kundenserver\.de$", "ionos"),
    (r"(^|\.)gandi\.net$", "gandi"),
    (r"(^|\.)yahoodns\.net$", "yahoo"),
    (r"(^|\.)messagingengine\.com$", "fastmail"),
    (r"(^|\.)mail\.icloud\.com$", "icloud"),
    (r"(^|\.)yandex\.(net|ru)$", "yandex"),
    (r"^inbound-smtp\.[a-z0-9-]+\.amazonaws\.com$", "amazon"),
    (r"(^|\.)emailsrvr\.com$", "rackspace"),
    (r"(^|\.)secureserver\.net$", "godaddy"),
    (r"(^|\.)rzone\.de$", "strato"),
    (r"(^|\.)infomaniak\.ch$", "infomaniak"),
    (r"(^|\.)(titan\.email|hostinger\.com)$", "titan"),
    (r"(^|\.)mimecast(-offshore)?\.(com|co\.za)$", "gateway"),
    (r"(^|\.)pp(e)?-?hosted\.com$", "gateway"),
    (r"(^|\.)barracudanetworks\.com$", "gateway"),
    (r"(^|\.)iphmx\.com$", "gateway"),
    (r"(^|\.)tmes\.trendmicro\.(com|eu)$", "gateway"),
    (r"(^|\.)hydra\.sophos\.com$", "gateway"),
    (r"(^|\.)mailcontrol\.com$", "gateway"),
    (r"(^|\.)messagelabs\.com$", "gateway"),
    (r"(^|\.)hornetsecurity\.com$", "gateway"),
    (r"(^|\.)antispamcloud\.com$", "gateway"),
    (r"(^|\.)spamexperts\.(com|eu|net)$", "gateway"),
    (r"(^|\.)mailanyone\.net$", "gateway"),
)
_MX_COMPILED = tuple((re.compile(rx), slug) for rx, slug in _MX_RULES)


def classify_mx(hosts: list[str], domain: str = "") -> Provider:
    """Provider behind a domain from its MX hosts (best preference first)."""
    for host in hosts:
        h = host.lower().rstrip(".")
        for rx, slug in _MX_COMPILED:
            if rx.search(h):
                return PROVIDERS[slug]
    if domain and any(
        h.lower().rstrip(".") == domain or h.lower().rstrip(".").endswith("." + domain)
        for h in hosts
    ):
        return PROVIDERS["self"]
    return PROVIDERS["other"]


def provider(slug: str | None) -> Provider:
    return PROVIDERS.get(slug or "", PROVIDERS["other"])


DISPOSABLE_DOMAINS = frozenset(
    """
mailinator.com guerrillamail.com guerrillamail.net sharklasers.com 10minutemail.com
tempmail.com temp-mail.org yopmail.com yopmail.fr trashmail.com getnada.com dispostable.com
maildrop.cc throwawaymail.com fakeinbox.com mintemail.com mohmal.com emailondeck.com
tempr.email discard.email mailnesia.com mytemp.email burnermail.io moakt.com tempail.com
spamgourmet.com jetable.org mailcatch.com spambox.us trash-mail.com tempinbox.com
33mail.com guerrillamailblock.com grr.la pokemail.net spam4.me
""".split()
)
FREE_MAIL_DOMAINS = frozenset(
    """
gmail.com googlemail.com outlook.com hotmail.com hotmail.fr hotmail.co.uk hotmail.de live.com
live.fr msn.com yahoo.com yahoo.fr yahoo.co.uk yahoo.de yahoo.es ymail.com aol.com icloud.com
me.com mac.com proton.me protonmail.com pm.me gmx.de gmx.net gmx.com gmx.at gmx.ch web.de
t-online.de freenet.de orange.fr wanadoo.fr free.fr sfr.fr laposte.net libero.it virgilio.it
tiscali.it alice.it mail.ru yandex.ru yandex.com rambler.ru qq.com 163.com 126.com sina.com
naver.com daum.net hanmail.net rediffmail.com zoho.com zohomail.com mail.com fastmail.com
tutanota.com tuta.io seznam.cz wp.pl o2.pl onet.pl interia.pl bluewin.ch telenet.be
skynet.be hetnet.nl ziggo.nl kpnmail.nl btinternet.com sky.com virginmedia.com
""".split()
)


def _in(domain: str, table: frozenset[str]) -> bool:
    parts = domain.lower().split(".")
    return any(".".join(parts[i:]) in table for i in range(len(parts) - 1))


def is_disposable(domain: str) -> bool:
    """A throwaway-address service (mailinator, yopmail, ...): never a work address."""
    return _in(domain, DISPOSABLE_DOMAINS)


def is_free_mail(domain: str) -> bool:
    """A consumer mailbox provider (gmail.com, gmx.de, ...): not a company domain."""
    return domain.lower() in FREE_MAIL_DOMAINS
