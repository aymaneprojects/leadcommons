"""Verification (DNS + optional SMTP probe), confidence scoring and the public pipeline."""

from __future__ import annotations

import smtplib
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import dns.resolver
import pytest
import respx

from leadcommons import enrich
from leadcommons.enrich import crawl, verify
from leadcommons.enrich.score import EXACT_POINTS, confidence, pattern_points
from leadcommons.models import EmailStatus, Person, make_id

FIXTURES = Path(__file__).parent / "fixtures" / "enrich"
DOMAIN = "acme.example"
TARGET = "jane.doe@acme.example"
MX = [
    SimpleNamespace(preference=20, exchange="mx2.acme.example."),
    SimpleNamespace(preference=10, exchange="mx1.acme.example."),
]
REJECT = (550, b"5.1.1 User unknown")


class FakeSMTP:
    """Records the SMTP dialogue; replies come from the class attributes set per test."""

    replies: dict[str, tuple[int, bytes]] = {}
    default: tuple[int, bytes] = REJECT
    connect_error: Exception | None = None
    sessions: list[FakeSMTP] = []

    def __init__(self, host: str = "", port: int = 0, timeout: float | None = None) -> None:
        if self.connect_error:
            raise self.connect_error
        self.host, self.port, self.timeout = host, port, timeout
        self.log: list[str] = []
        self.sessions.append(self)

    def ehlo(self, name: str = "") -> tuple[int, bytes]:
        self.log.append("EHLO")
        return 250, b"hello"

    def mail(self, sender: str, options: tuple = ()) -> tuple[int, bytes]:
        self.log.append(f"MAIL FROM:<{sender}>")
        return 250, b"ok"

    def rcpt(self, recip: str, options: tuple = ()) -> tuple[int, bytes]:
        self.log.append(f"RCPT TO:<{recip}>")
        return self.replies.get(recip, self.default)

    def quit(self) -> tuple[int, bytes]:
        self.log.append("QUIT")
        return 221, b"bye"

    def close(self) -> None:
        self.log.append("CLOSE")

    def data(self, *args: object) -> None:
        raise AssertionError("DATA must never be sent")

    sendmail = send_message = data


SmtpFactory = Callable[..., list[FakeSMTP]]


@pytest.fixture
def smtp(monkeypatch: pytest.MonkeyPatch) -> SmtpFactory:
    def install(
        replies: dict[str, tuple[int, bytes]] | None = None,
        default: tuple[int, bytes] = REJECT,
        connect_error: Exception | None = None,
    ) -> list[FakeSMTP]:
        sessions: list[FakeSMTP] = []
        attrs = {
            "replies": replies or {},
            "default": default,
            "connect_error": connect_error,
            "sessions": sessions,
        }
        monkeypatch.setattr(smtplib, "SMTP", type("Fake", (FakeSMTP,), attrs))
        return sessions

    return install


@pytest.fixture
def dns_table(monkeypatch: pytest.MonkeyPatch) -> dict[tuple[str, str], object]:
    """Fake resolver: map (name, rdtype) to an answer list or an exception; NXDOMAIN otherwise."""
    table: dict[tuple[str, str], object] = {(DOMAIN, "MX"): MX}

    def resolve(qname: str, rdtype: str = "A", **kwargs: object) -> object:
        assert kwargs.get("lifetime") == verify.DNS_LIFETIME
        value = table.get((str(qname), rdtype), dns.resolver.NXDOMAIN())
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(dns.resolver, "resolve", resolve)
    return table


@pytest.fixture(autouse=True)
def _fresh_crawl(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(crawl, "CRAWL_DELAY", 0.0)
    crawl.clear_cache()
    yield
    crawl.clear_cache()


# ---------------------------------------------------------------- verification


def test_bad_syntax_is_invalid(dns_table) -> None:
    cand = enrich.verify_email("not an email")
    assert cand.status is EmailStatus.INVALID
    assert cand.checks["syntax"] == "fail"
    assert cand.checks["mx"] == "skipped"
    assert cand.confidence == 0


@pytest.mark.parametrize(
    ("mx_answer", "a_answer", "status", "mx_check"),
    [
        (dns.resolver.NXDOMAIN(), None, EmailStatus.INVALID, "fail"),
        ([SimpleNamespace(preference=0, exchange=".")], None, EmailStatus.INVALID, "fail"),
        (dns.resolver.NoAnswer(), dns.resolver.NoAnswer(), EmailStatus.INVALID, "fail"),
        (dns.resolver.NoAnswer(), [SimpleNamespace(address="192.0.2.1")], EmailStatus.UNKNOWN, "a"),
        (dns.resolver.LifetimeTimeout(), None, EmailStatus.UNKNOWN, "error"),
        (MX, None, EmailStatus.UNKNOWN, "ok"),
    ],
)
def test_dns_status_mapping(dns_table, smtp, mx_answer, a_answer, status, mx_check) -> None:
    sessions = smtp()
    dns_table[(DOMAIN, "MX")] = mx_answer
    if a_answer is not None:
        dns_table[(DOMAIN, "A")] = a_answer
    cand = enrich.verify_email(TARGET)
    assert cand.status is status
    assert cand.checks["mx"] == mx_check
    assert cand.checks["smtp"] == "skipped"  # no probe unless asked
    assert sessions == []


def test_no_probe_result(dns_table) -> None:
    cand = enrich.verify_email("Jane.Doe@ACME.example")
    assert cand.address == TARGET
    assert cand.status is EmailStatus.UNKNOWN
    assert cand.checks == {
        "syntax": "ok",
        "mx": "ok",
        "smtp": "skipped",
        "catch_all": "skipped",
        "role": "no",
        "disposable": "no",
    }
    assert cand.confidence == 20


def test_probe_valid_mailbox(dns_table, smtp) -> None:
    sessions = smtp({TARGET: (250, b"2.1.5 ok")})
    cand = enrich.verify_email(TARGET, smtp_probe=True)
    assert cand.status is EmailStatus.VALID
    assert (cand.checks["smtp"], cand.checks["catch_all"]) == ("ok", "no")
    assert cand.confidence >= 90
    (session,) = sessions
    assert (session.host, session.port, session.timeout) == ("mx1.acme.example", 25, 10.0)
    assert session.log[:3] == ["EHLO", "MAIL FROM:<>", f"RCPT TO:<{TARGET}>"]
    random_rcpt = session.log[3]
    assert random_rcpt.endswith("@acme.example>") and TARGET not in random_rcpt
    assert session.log[4:] == ["QUIT"]


def test_probe_catch_all(dns_table, smtp) -> None:
    smtp(default=(250, b"ok"))
    cand = enrich.verify_email(TARGET, smtp_probe=True)
    assert cand.status is EmailStatus.CATCH_ALL
    assert cand.checks["catch_all"] == "yes"


@pytest.mark.parametrize(
    ("reply", "status", "smtp_check"),
    [
        ((550, b"5.1.1 User unknown"), EmailStatus.INVALID, "fail"),
        ((554, b"5.7.1 Client host blocked using Spamhaus"), EmailStatus.UNKNOWN, "blocked"),
        ((451, b"4.7.1 Greylisted, try again later"), EmailStatus.UNKNOWN, "temp"),
    ],
)
def test_probe_rejections(dns_table, smtp, reply, status, smtp_check) -> None:
    sessions = smtp({TARGET: reply})
    cand = enrich.verify_email(TARGET, smtp_probe=True)
    assert (cand.status, cand.checks["smtp"]) == (status, smtp_check)
    assert sessions[0].log == ["EHLO", "MAIL FROM:<>", f"RCPT TO:<{TARGET}>", "QUIT"]


def test_probe_connection_error(dns_table, smtp) -> None:
    smtp(connect_error=ConnectionRefusedError("port 25 blocked"))
    cand = enrich.verify_email(TARGET, smtp_probe=True)
    assert cand.status is EmailStatus.UNKNOWN
    assert cand.checks["smtp"] == "error"


def test_probe_uses_a_record_host(dns_table, smtp) -> None:
    dns_table[(DOMAIN, "MX")] = dns.resolver.NoAnswer()
    dns_table[(DOMAIN, "A")] = [SimpleNamespace(address="192.0.2.1")]
    sessions = smtp({TARGET: (250, b"ok")})
    assert enrich.verify_email(TARGET, smtp_probe=True).status is EmailStatus.VALID
    assert sessions[0].host == DOMAIN


def test_role_and_disposable_flags(dns_table) -> None:
    dns_table[("mailinator.com", "MX")] = MX
    cand = enrich.verify_email("info@mailinator.com")
    assert (cand.checks["role"], cand.checks["disposable"]) == ("yes", "yes")
    assert cand.confidence == 0


def test_confidence_ordering() -> None:
    mx_ok = {"mx": "ok"}
    unknown = EmailStatus.UNKNOWN
    valid = confidence(0, EmailStatus.VALID, mx_ok)
    published = confidence(EXACT_POINTS, unknown, mx_ok)
    two_matches = confidence(pattern_points(2, 1.0, True), unknown, mx_ok)
    one_match = confidence(pattern_points(1, 1.0, True), unknown, mx_ok)
    best_prior = confidence(pattern_points(0, 1.0, False), unknown, mx_ok)
    other_prior = confidence(pattern_points(0, 0.4, False), unknown, mx_ok)
    catch_all = confidence(pattern_points(2, 1.0, True), EmailStatus.CATCH_ALL, mx_ok)
    invalid = confidence(EXACT_POINTS, EmailStatus.INVALID, mx_ok)
    assert valid > published > two_matches > one_match > best_prior > other_prior > invalid == 0
    assert two_matches > catch_all
    assert confidence(EXACT_POINTS, EmailStatus.VALID, mx_ok) == 100
    assert confidence(20, unknown, {"mx": "a"}) < confidence(20, unknown, mx_ok)


# ---------------------------------------------------------------- public pipeline


def mock_acme(router: respx.MockRouter) -> respx.Route:
    """Homepage linking to a JSON-LD team page that shows two personal addresses."""
    router.get("https://acme.example/robots.txt").respond(404)
    home = router.get("https://acme.example/").respond(
        200, html='<a href="/team">Our team</a> <a href="/careers">Careers</a>'
    )
    router.get("https://acme.example/team").respond(
        200, html=(FIXTURES / "team_jsonld.html").read_text(encoding="utf-8")
    )
    return home


def priya() -> Person:
    return Person(id=make_id("pe", DOMAIN, "Priya Patel"), full_name="Priya Patel")


def test_find_people_and_public_emails_share_one_crawl() -> None:
    with respx.mock(assert_all_called=False) as router:
        home = mock_acme(router)
        people = enrich.find_people(DOMAIN)
        emails = enrich.find_public_emails(DOMAIN)
        assert home.call_count == 1
    assert {p.full_name for p in people} >= {"Jane Doe", "Priya Patel"}
    assert all(p.source_urls == ["https://acme.example/team"] for p in people)
    assert emails == ["robert.smith@acme.example", "hello@acme.example", "jane.doe@acme.example"]
    people[0].source_urls.append("mutated")  # callers get copies
    assert "mutated" not in enrich.find_people(DOMAIN)[0].source_urls


def test_find_email_infers_pattern_from_the_site(dns_table, smtp) -> None:
    sessions = smtp()
    with respx.mock(assert_all_called=False) as router:
        mock_acme(router)
        cand = enrich.find_email(priya(), DOMAIN)
    assert cand is not None
    assert cand.address == "priya.patel@acme.example"
    assert cand.pattern == "{first}.{last}"  # jane.doe + robert.smith confirm it
    assert cand.status is EmailStatus.UNKNOWN
    assert 55 < cand.confidence < 70
    assert sessions == []


def test_find_email_probe_stops_at_first_valid(dns_table, smtp) -> None:
    sessions = smtp({"priya@acme.example": (250, b"ok")})
    with respx.mock(assert_all_called=False) as router:
        mock_acme(router)
        cand = enrich.find_email(priya(), DOMAIN, smtp_probe=True)
    assert cand is not None
    assert (cand.address, cand.status, cand.pattern) == (
        "priya@acme.example",
        EmailStatus.VALID,
        "{first}",
    )
    assert cand.confidence >= 90
    (session,) = sessions  # one SMTP session for all candidates
    rcpts = [line for line in session.log if line.startswith("RCPT")]
    assert rcpts[:2] == ["RCPT TO:<priya.patel@acme.example>", "RCPT TO:<priya@acme.example>"]
    assert len(rcpts) == 3  # + the random catch-all check, never the third candidate
    assert "RCPT TO:<ppatel@acme.example>" not in rcpts


def test_find_email_none_without_mail_domain(dns_table) -> None:
    dns_table[(DOMAIN, "MX")] = dns.resolver.NXDOMAIN()
    with respx.mock(assert_all_called=False) as router:
        mock_acme(router)
        assert enrich.find_email(priya(), DOMAIN) is None


def test_find_email_without_crawlable_site_uses_priors(dns_table) -> None:
    with respx.mock(assert_all_called=False) as router:
        router.route().respond(404)
        cand = enrich.find_email(priya(), DOMAIN)
    assert cand is not None
    assert cand.address == "priya.patel@acme.example"
    assert cand.confidence == 40  # best global guess (20) + MX (20)
