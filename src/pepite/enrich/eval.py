"""Measure the email finder on a gold set: precision, coverage, calibration, per provider.

Gold set: a CSV with `full_name, domain, true_address` (other columns are ignored), one row per
person whose real work address is known.

For each row the finder runs as it would for a user, with one change: the person's own address
is removed from the site evidence (leave-one-out), because the gold addresses were collected
from the companies' own pages and the task we measure is "find the address of someone whose
address is not published".

Site evidence and network come from one of two places:

- a world file (`<gold>.world.json` next to the CSV, or `--world`): per domain the names and
  addresses the public pages show (`site_names`, `site_emails`), replayed instead of crawling.
  When an entry also has `mx` the DNS answers are simulated, and with `smtp`
  (`reject_unknown`, `accept_all`, `block`, `greylist`: 4xx on the first try of each address,
  `throttle`: one recipient per connection) plus `mailboxes` the mail server is
  simulated too, so `--smtp` runs the real verification code against a fake server. `provider`
  and `segment` label the report; `aliases` lists other addresses of the same person.
- otherwise the live web: each domain is crawled politely (robots.txt, rate limit), DNS is
  real and SMTP is probed only with `--smtp`, when settings allow it (never in demo mode).

Reported for the top-ranked guess, for the address we would show, and for the addresses we
would show as "valid": precision (share correct), coverage (share of all rows answered), the
calibration of the 0-100 confidence (buckets, expected calibration error, Brier score), and the
same per mail provider, per segment and per amount of site evidence.

`build_gold` makes a real gold set from public pages: names and addresses published side by
side on a company's own site (same card or block, or adjacent lines). The result holds personal
data of real people: keep it on your machine, never commit or share it.
"""

from __future__ import annotations

import contextlib
import csv
import json
import math
import random
import re
import smtplib
from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

import dns.resolver

from pepite.models import EmailCandidate

BUCKETS: tuple[tuple[int, int], ...] = ((0, 20), (20, 40), (40, 60), (60, 80), (80, 90), (90, 101))


# ----------------------------------------------------------------------------- inputs


@dataclass(frozen=True)
class GoldRow:
    full_name: str
    domain: str
    true_address: str


@dataclass
class DomainWorld:
    site_names: list[str] = field(default_factory=list)
    site_emails: list[str] = field(default_factory=list)
    mx: list[str] | None = None  # None: use real DNS
    smtp: str | None = None  # None: use the real network (only with --smtp)
    mailboxes: set[str] = field(default_factory=set)  # local parts that exist
    aliases: dict[str, list[str]] = field(default_factory=dict)
    provider: str | None = None
    segment: str | None = None


def load_gold(path: str | Path) -> list[GoldRow]:
    """Read a gold CSV (utf-8, header with full_name, domain, true_address)."""
    rows: list[GoldRow] = []
    with Path(path).open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        missing = {"full_name", "domain", "true_address"} - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"gold file lacks column(s): {', '.join(sorted(missing))}")
        for raw in reader:
            name = (raw.get("full_name") or "").strip()
            domain = (raw.get("domain") or "").strip().lower()
            true = (raw.get("true_address") or "").strip().lower()
            if name and domain and "@" in true:
                rows.append(GoldRow(name, domain, true))
    return rows


def world_path_for(gold: str | Path) -> Path:
    """`gold.csv` -> `gold.world.json` (the default world file location)."""
    return Path(gold).with_suffix(".world.json")


def load_world(path: str | Path) -> dict[str, DomainWorld]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    out: dict[str, DomainWorld] = {}
    for domain, e in (data.get("domains") or {}).items():
        out[domain.lower()] = DomainWorld(
            site_names=list(e.get("site_names") or []),
            site_emails=[x.lower() for x in e.get("site_emails") or []],
            mx=list(e["mx"]) if "mx" in e else None,
            smtp=e.get("smtp"),
            mailboxes={x.lower() for x in e.get("mailboxes") or []},
            aliases={
                k.lower(): [a.lower() for a in v] for k, v in (e.get("aliases") or {}).items()
            },
            provider=e.get("provider"),
            segment=e.get("segment"),
        )
    return out


# ----------------------------------------------------------------------------- simulation


class _SimServer:
    """Fake mail servers answering from a world file (one instance per evaluation run)."""

    REJECT = {
        "google": b"5.1.1 The email account that you tried to reach does not exist.",
        "microsoft": b"5.4.1 Recipient address rejected: Access denied. AS(201806281)",
    }
    BLOCK = {
        "microsoft": b"5.7.606 Access denied, banned sending IP [192.0.2.1].",
    }

    def __init__(self, world: dict[str, DomainWorld]) -> None:
        self.world = world
        self.greylisted: set[str] = set()
        self.sessions = 0
        self.rcpts = 0

    def rcpt(self, address: str, nth: int = 1) -> tuple[int, bytes]:
        """Answer the `nth` RCPT of a session for `address`."""
        self.rcpts += 1
        local, _, domain = address.lower().rpartition("@")
        w = self.world.get(domain)
        if w is None or w.smtp is None:
            return 550, b"5.7.1 Relaying denied"
        if w.smtp == "throttle" and nth > 1:  # one recipient per connection, then "busy"
            return 451, b"4.7.500 Server busy. Please try again later."
        if w.smtp == "block":
            return 554, self.BLOCK.get(
                w.provider or "", b"5.7.1 Client host blocked using Spamhaus"
            )
        if w.smtp == "accept_all":
            return 250, b"2.1.5 Recipient OK"
        if w.smtp == "greylist" and address.lower() not in self.greylisted:
            self.greylisted.add(address.lower())
            return 451, b"4.7.1 Greylisted, please try again later"
        if local in w.mailboxes:
            return 250, b"2.1.5 Recipient OK"
        return 550, self.REJECT.get(
            w.provider or "", b"5.1.1 User unknown in virtual mailbox table"
        )

    def smtp_class(self) -> type:
        server = self

        class SimSMTP:
            def __init__(self, host: str = "", port: int = 0, timeout: float | None = None, **_):
                server.sessions += 1
                self.host = host
                self.n = 0

            def ehlo(self, name: str = "") -> tuple[int, bytes]:
                return 250, b"sim.example"

            helo = ehlo

            def mail(self, sender: str, options: Sequence[str] = ()) -> tuple[int, bytes]:
                return 250, b"2.1.0 Sender OK"

            def rcpt(self, recip: str, options: Sequence[str] = ()) -> tuple[int, bytes]:
                self.n += 1
                return server.rcpt(recip, self.n)

            def rset(self) -> tuple[int, bytes]:
                return 250, b"2.0.0 OK"

            def quit(self) -> tuple[int, bytes]:
                return 221, b"2.0.0 Bye"

            def close(self) -> None:
                return None

        return SimSMTP


@contextlib.contextmanager
def simulated_network(world: dict[str, DomainWorld], *, smtp: bool) -> Iterator[_SimServer]:
    """Patch DNS (domains with `mx`) and SMTP (domains with `smtp`) to answer from `world`."""
    from pepite.enrich import verify

    server = _SimServer(world)
    real_resolve = dns.resolver.resolve

    def resolve(qname: Any, rdtype: Any = "A", *args: Any, **kwargs: Any) -> Any:
        name = str(qname).rstrip(".").lower()
        w = world.get(name)
        if w is None or w.mx is None:
            return real_resolve(qname, rdtype, *args, **kwargs)
        if str(rdtype) == "MX":
            if not w.mx:
                raise dns.resolver.NoAnswer()
            return [
                SimpleNamespace(preference=10 * (i + 1), exchange=h + ".")
                for i, h in enumerate(w.mx)
            ]
        if str(rdtype) == "A":
            return [SimpleNamespace(address="192.0.2.10")]
        raise dns.resolver.NoAnswer()

    with contextlib.ExitStack() as stack:
        stack.enter_context(mock.patch.object(dns.resolver, "resolve", resolve))
        if smtp and any(w.smtp for w in world.values()):
            stack.enter_context(mock.patch.object(smtplib, "SMTP", server.smtp_class()))
        if hasattr(verify, "_sleep"):  # greylisting back-off and per-MX pacing: no real waits
            stack.enter_context(mock.patch.object(verify, "_sleep", lambda _s: None))
        if hasattr(verify, "reset_rate_limits"):
            verify.reset_rate_limits()
        yield server


# ----------------------------------------------------------------------------- one row


@dataclass
class RowResult:
    full_name: str
    domain: str
    true_address: str
    top_guess: str | None
    top_correct: bool
    true_rank: int | None  # 1-based position of the true address among our guesses
    shown: str | None  # what find_email would return
    status: str | None
    confidence: int | None
    shown_correct: bool
    shown_deliverable: bool  # the true address or another address of the same person
    raw_probability: float | None
    provider: str
    segment: str
    evidence: int  # personal addresses on the site, the person's own one held out
    basis: str = ""

    @property
    def valid(self) -> bool:
        return self.status == "valid"


def _personal_count(emails: Iterable[str], domain: str) -> int:
    from pepite.enrich.patterns import is_role_mailbox

    n = 0
    for e in emails:
        local, _, host = e.rpartition("@")
        if (host == domain or host.endswith("." + domain)) and not is_role_mailbox(local):
            n += 1
    return n


def _run_row(
    row: GoldRow,
    names: Sequence[str],
    emails: Sequence[str],
    world: DomainWorld | None,
    smtp: bool,
) -> RowResult:
    from pepite.enrich import finder
    from pepite.enrich.patterns import rank_guesses

    known = [e for e in emails if e.lower() != row.true_address]
    guesses = rank_guesses(row.full_name, row.domain, known, names)
    addresses = [g.address for g in guesses]
    rank = addresses.index(row.true_address) + 1 if row.true_address in addresses else None
    detail = getattr(finder, "find_email_detail", None)
    raw: float | None = None
    if detail is not None:
        found = detail(row.full_name, row.domain, known, names, smtp_probe=smtp)
        cand: EmailCandidate | None = found.candidate
        raw = found.raw_probability
    else:
        cand = finder.find_email_from(row.full_name, row.domain, known, names, smtp_probe=smtp)
    shown = cand.address if cand else None
    aliases = set(world.aliases.get(row.true_address, [])) if world else set()
    status = str(getattr(cand.status, "value", cand.status)) if cand else None
    checks = cand.checks if cand else {}
    provider = (world.provider if world else None) or checks.get("provider") or "unknown"
    return RowResult(
        full_name=row.full_name,
        domain=row.domain,
        true_address=row.true_address,
        top_guess=addresses[0] if addresses else None,
        top_correct=bool(addresses) and addresses[0] == row.true_address,
        true_rank=rank,
        shown=shown,
        status=status,
        confidence=cand.confidence if cand else None,
        shown_correct=shown == row.true_address,
        shown_deliverable=shown is not None and (shown == row.true_address or shown in aliases),
        raw_probability=raw,
        provider=provider,
        segment=(world.segment if world else None) or "-",
        evidence=_personal_count(known, row.domain),
        basis=checks.get("basis", ""),
    )


# ----------------------------------------------------------------------------- metrics


def _pct(num: int, den: int) -> float | None:
    return num / den if den else None


@dataclass
class Summary:
    n: int
    top_precision: float | None
    top_coverage: float | None
    shown_precision: float | None
    shown_coverage: float | None
    deliverable_precision: float | None
    valid_n: int
    valid_precision: float | None
    valid_coverage: float | None
    recall_at_3: float | None


def summarise(results: Sequence[RowResult]) -> Summary:
    n = len(results)
    tops = [r for r in results if r.top_guess]
    shown = [r for r in results if r.shown]
    valid = [r for r in results if r.valid]
    return Summary(
        n=n,
        top_precision=_pct(sum(r.top_correct for r in tops), len(tops)),
        top_coverage=_pct(len(tops), n),
        shown_precision=_pct(sum(r.shown_correct for r in shown), len(shown)),
        shown_coverage=_pct(len(shown), n),
        deliverable_precision=_pct(sum(r.shown_deliverable for r in shown), len(shown)),
        valid_n=len(valid),
        valid_precision=_pct(sum(r.shown_correct for r in valid), len(valid)),
        valid_coverage=_pct(len(valid), n),
        recall_at_3=_pct(sum(1 for r in results if r.true_rank and r.true_rank <= 3), n),
    )


@dataclass
class Bucket:
    low: int
    high: int
    n: int
    mean_confidence: float | None
    accuracy: float | None


def calibration(results: Sequence[RowResult]) -> tuple[list[Bucket], float | None, float | None]:
    """Buckets of shown confidence vs accuracy, expected calibration error (points), Brier."""
    shown = [r for r in results if r.shown and r.confidence is not None]
    buckets: list[Bucket] = []
    ece = 0.0
    for low, high in BUCKETS:
        inside = [r for r in shown if low <= (r.confidence or 0) < high]
        if not inside:
            buckets.append(Bucket(low, high, 0, None, None))
            continue
        conf = sum(r.confidence or 0 for r in inside) / len(inside)
        acc = sum(r.shown_correct for r in inside) / len(inside)
        ece += len(inside) / len(shown) * abs(acc * 100 - conf)
        buckets.append(Bucket(low, high, len(inside), conf, acc))
    if not shown:
        return buckets, None, None
    brier = sum(((r.confidence or 0) / 100 - r.shown_correct) ** 2 for r in shown) / len(shown)
    return buckets, ece, brier


def valid_interval(
    results: Sequence[RowResult], rounds: int = 2000, seed: int = 7
) -> tuple[float, float] | None:
    """5th and 95th percentile of "valid" precision when whole companies are resampled.

    Errors cluster by company (one company with odd addresses gives several), so resampling
    rows would overstate how sure the estimate is."""
    by_domain: dict[str, list[bool]] = defaultdict(list)
    for r in results:
        if r.valid:
            by_domain[r.domain].append(r.shown_correct)
    domains = list(by_domain)
    if len(domains) < 5:
        return None
    rng = random.Random(seed)
    stats = []
    for _ in range(rounds):
        sample = [x for d in (rng.choice(domains) for _ in domains) for x in by_domain[d]]
        stats.append(sum(sample) / len(sample))
    stats.sort()
    return stats[int(0.05 * rounds)], stats[int(0.95 * rounds) - 1]


def _evidence_band(n: int) -> str:
    return "0" if n == 0 else "1" if n == 1 else "2-3" if n <= 3 else "4+"


def breakdown(results: Sequence[RowResult], key: Callable[[RowResult], str]) -> dict[str, Summary]:
    groups: dict[str, list[RowResult]] = defaultdict(list)
    for r in results:
        groups[key(r)].append(r)
    return {k: summarise(v) for k, v in sorted(groups.items(), key=lambda kv: -len(kv[1]))}


@dataclass
class Report:
    gold: str
    mode: dict[str, str]
    domains: int
    overall: Summary
    buckets: list[Bucket]
    ece: float | None
    brier: float | None
    by_provider: dict[str, Summary]
    by_segment: dict[str, Summary]
    by_evidence: dict[str, Summary]
    rows: list[RowResult]
    valid_interval: tuple[float, float] | None = None  # 90% interval, companies resampled

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["rows"] = [asdict(r) for r in self.rows]
        return out


def evaluate(
    rows: Sequence[GoldRow],
    *,
    world: dict[str, DomainWorld] | None = None,
    smtp: bool = False,
    gold_name: str = "",
    max_pages: int = 10,
    progress: Callable[[int, int], None] | None = None,
) -> Report:
    """Run the finder on every gold row and measure it (see module docstring)."""
    results: list[RowResult] = []
    site_cache: dict[str, tuple[list[str], list[str]]] = {}

    def evidence(domain: str) -> tuple[list[str], list[str]]:
        if world is not None and domain in world:
            w = world[domain]
            return w.site_names, w.site_emails
        if domain not in site_cache:
            from pepite.enrich import find_people, find_public_emails

            names = [p.full_name for p in find_people(domain, max_pages=max_pages)]
            site_cache[domain] = (names, find_public_emails(domain, max_pages=max_pages))
        return site_cache[domain]

    smtp_allowed = smtp
    if smtp and world is None:
        from pepite.enrich.verify import probing_allowed

        smtp_allowed = probing_allowed(True)
    ctx = simulated_network(world, smtp=smtp) if world else contextlib.nullcontext()
    with ctx:
        for i, row in enumerate(rows):
            names, emails = evidence(row.domain)
            w = world.get(row.domain) if world else None
            results.append(_run_row(row, names, emails, w, smtp_allowed))
            if progress:
                progress(i + 1, len(rows))
    buckets, ece, brier = calibration(results)
    simulated_dns = bool(world) and all(w.mx is not None for w in world.values())
    simulated_smtp = bool(world) and all(w.smtp for w in world.values())
    mode = {
        "site evidence": "replayed from world file" if world else "crawled live",
        "dns": "simulated" if simulated_dns else "live",
        "smtp": ("simulated" if simulated_smtp else "live") if smtp_allowed else "off",
    }
    return Report(
        gold=gold_name,
        mode=mode,
        domains=len({r.domain for r in rows}),
        overall=summarise(results),
        buckets=buckets,
        ece=ece,
        brier=brier,
        by_provider=breakdown(results, lambda r: r.provider),
        by_segment=breakdown(results, lambda r: r.segment),
        by_evidence=breakdown(results, lambda r: _evidence_band(r.evidence)),
        rows=results,
        valid_interval=valid_interval(results),
    )


# ----------------------------------------------------------------------------- output


def _f(x: float | None) -> str:
    return "   -  " if x is None else f"{100 * x:5.1f}%"


def _interval(iv: tuple[float, float] | None) -> str:
    if iv is None:
        return ""
    return f"; 90% interval {100 * iv[0]:.1f}-{100 * iv[1]:.1f}% resampling companies"


def _table(title: str, groups: dict[str, Summary]) -> list[str]:
    lines = [
        "",
        title,
        f"  {'':<14}{'n':>5}  {'top guess':>9}  {'shown':>7}  {'valid n':>7}  "
        f"{'valid prec':>10}  {'valid cov':>9}",
    ]
    for name, s in groups.items():
        lines.append(
            f"  {name[:14]:<14}{s.n:>5}  {_f(s.top_precision):>9}  {_f(s.shown_precision):>7}  "
            f"{s.valid_n:>7}  {_f(s.valid_precision):>10}  {_f(s.valid_coverage):>9}"
        )
    return lines


def format_report(report: Report) -> str:
    o = report.overall
    mode = ", ".join(f"{k}: {v}" for k, v in report.mode.items())
    lines = [
        f"Email evaluation: {o.n} people at {report.domains} domains ({report.gold})",
        f"Own address held out from the site evidence. {mode}.",
        "",
        f"  {'':<22}{'precision':>10}{'coverage':>10}",
        f"  {'Top-ranked guess':<22}{_f(o.top_precision):>10}{_f(o.top_coverage):>10}",
        f"  {'Shown address':<22}{_f(o.shown_precision):>10}{_f(o.shown_coverage):>10}",
        f"  {'Shown as valid':<22}{_f(o.valid_precision):>10}{_f(o.valid_coverage):>10}"
        f"   ({o.valid_n} addresses{_interval(report.valid_interval)})",
        f"  Shown address reaches the person (true address or a known alias): "
        f"{_f(o.deliverable_precision).strip()}",
        f"  True address among our first 3 guesses: {_f(o.recall_at_3).strip()}",
        "",
        "Calibration of the shown confidence",
        f"  {'confidence':<12}{'n':>5}{'mean':>8}{'correct':>9}",
    ]
    for b in report.buckets:
        label = f"{b.low}-{min(b.high, 100) - (0 if b.high > 100 else 1)}"
        mean = "    -" if b.mean_confidence is None else f"{b.mean_confidence:5.1f}"
        lines.append(f"  {label:<12}{b.n:>5}{mean:>8}{_f(b.accuracy):>9}")
    if report.ece is not None:
        lines.append(
            f"  Expected calibration error {report.ece:.1f} points, Brier score {report.brier:.3f}"
        )
    lines += _table("By mail provider", report.by_provider)
    if len(report.by_segment) > 1:
        lines += _table("By segment", report.by_segment)
    lines += _table("By personal addresses on the site (own address held out)", report.by_evidence)
    return "\n".join(lines)


def write_details(report: Report, path: str | Path) -> None:
    """One CSV line per gold row, for error analysis."""
    fields = list(asdict(report.rows[0]).keys()) if report.rows else ["full_name"]
    with Path(path).open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(asdict(r) for r in report.rows)


def fit_calibration(
    results: Sequence[RowResult], bins: int = 15
) -> tuple[tuple[float, float], ...] | None:
    """Isotonic calibration knots (raw posterior, observed accuracy) for score.CALIBRATION.

    Shown rows are sorted by raw posterior and cut into `bins` equal groups; adjacent groups
    are pooled while accuracy would decrease (pool-adjacent-violators), so the map is monotone.
    Needs raw probabilities (the current finder) and at least 50 shown rows."""
    pts = sorted(
        (r.raw_probability, 1.0 if r.shown_correct else 0.0)
        for r in results
        if r.shown and r.raw_probability is not None
    )
    if len(pts) < 50:
        return None
    size = math.ceil(len(pts) / bins)
    blocks: list[list[float]] = []  # [mean raw, accuracy, n]
    for i in range(0, len(pts), size):
        chunk = pts[i : i + size]
        n = float(len(chunk))
        blocks.append([sum(x for x, _ in chunk) / n, sum(y for _, y in chunk) / n, n])
    pooled: list[list[float]] = []
    for b in blocks:
        pooled.append(b)
        while len(pooled) > 1 and pooled[-2][1] >= pooled[-1][1]:
            b2, b1 = pooled.pop(), pooled.pop()
            n = b1[2] + b2[2]
            pooled.append(
                [(b1[0] * b1[2] + b2[0] * b2[2]) / n, (b1[1] * b1[2] + b2[1] * b2[2]) / n, n]
            )
    return tuple((round(x, 4), round(y, 4)) for x, y, _ in pooled)


# ----------------------------------------------------------------------------- gold builder


@dataclass(frozen=True)
class GoldPair:
    full_name: str
    domain: str
    true_address: str
    source_url: str


def _name_in(text: str, name: str) -> bool:
    from pepite.enrich.names import name_key

    key = name_key(name)
    return bool(key) and f" {key} " in f" {name_key(text)} "


def _plausible_owner(local: str, name: str) -> bool:
    """Loose check that an address can belong to a name: it contains the initial of the given
    name or of a surname token. Stops pairing a name with a colleague's address next to it,
    without favouring any pattern (nicknames like bob.smith still pass)."""
    from pepite.enrich.names import name_key

    tokens = name_key(name).split()
    letters = re.sub(r"[^a-z]", "", local.lower())
    return bool(tokens) and any(t[:3] in letters or letters.startswith(t[0]) for t in tokens)


def pair_names_and_addresses(
    pages: Sequence[Any], domain: str, names: Sequence[str]
) -> list[GoldPair]:
    """Names and personal addresses published side by side (same block, or within two lines)."""
    from pepite.enrich.emails import addresses_in_text
    from pepite.enrich.patterns import is_role_mailbox

    def personal(addresses: Iterable[str]) -> list[str]:
        return [a for a in addresses if not is_role_mailbox(a.rpartition("@")[0])]

    found: dict[str, set[tuple[str, str]]] = defaultdict(set)  # address -> {(name, url)}
    for page in pages:
        tree = page.tree()
        for a in tree.css("a[href]"):
            href = (a.attributes.get("href") or "").strip()
            if not href.lower().startswith("mailto:"):
                continue
            addrs = personal(addresses_in_text(href[7:].split("?")[0], domain))
            node = a
            for _ in range(5):  # smallest enclosing block that names exactly one person
                node = node.parent
                if node is None:
                    break
                inside = [n for n in names if _name_in(node.text(separator=" "), n)]
                if len(inside) == 1:
                    for addr in addrs:
                        found[addr].add((inside[0], page.url))
                if inside:
                    break
        lines = page.text.splitlines()
        for i, line in enumerate(lines):
            addrs = personal(addresses_in_text(line, domain))
            if len(addrs) != 1:
                continue
            window = lines[max(0, i - 2) : i + 1]
            for back, text in enumerate(reversed(window)):
                inside = [n for n in names if _name_in(text, n)]
                if len(inside) == 1:
                    found[addrs[0]].add((inside[0], page.url))
                if inside or (back and addresses_in_text(text, domain)):
                    break
    pairs: list[GoldPair] = []
    used_names: dict[str, int] = defaultdict(int)
    candidates = []
    for addr, owners in found.items():
        owner_names = {n for n, _ in owners}
        if len(owner_names) != 1:
            continue  # the same address next to two names: ambiguous
        name = owner_names.pop()
        if not _plausible_owner(addr.rpartition("@")[0], name):
            continue
        url = sorted(u for _, u in owners)[0]
        candidates.append(GoldPair(name, domain, addr, url))
        used_names[name] += 1
    pairs = [p for p in candidates if used_names[p.full_name] == 1]  # one address per person
    return pairs


def build_gold(
    domains: Iterable[str],
    out_csv: str | Path,
    *,
    max_pages: int = 15,
    progress: Callable[[str, int], None] | None = None,
) -> tuple[int, Path]:
    """Crawl each domain's public pages and write a gold CSV plus a world file for replay.

    Returns (rows written, world file path). The files contain personal data of real people."""
    from pepite.enrich import crawl
    from pepite.enrich.emails import extract_emails
    from pepite.enrich.people import extract_people

    out_csv = Path(out_csv)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    world: dict[str, Any] = {
        "version": 1,
        "note": "Real public data collected by `pepite eval build-gold`. Personal data: "
        "keep it local, do not commit or share.",
        "domains": {},
    }
    total = 0
    with out_csv.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["full_name", "domain", "true_address", "source"])
        writer.writeheader()
        for raw in domains:
            domain = crawl.normalise_domain(raw)
            if not domain or domain.startswith("#"):
                continue
            site = crawl.get_site(domain, max_pages=max_pages)
            names = [p.full_name for p in extract_people(site.pages, site.domain)]
            emails = extract_emails(site.pages, site.domain)
            pairs = pair_names_and_addresses(site.pages, site.domain, names)
            for p in pairs:
                writer.writerow(
                    {
                        "full_name": p.full_name,
                        "domain": p.domain,
                        "true_address": p.true_address,
                        "source": p.source_url,
                    }
                )
            if pairs:
                world["domains"][site.domain] = {"site_names": names, "site_emails": emails}
            total += len(pairs)
            if progress:
                progress(site.domain, len(pairs))
    path = world_path_for(out_csv)
    path.write_text(json.dumps(world, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return total, path
