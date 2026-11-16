"""Verification (DNS, provider, optional SMTP probe), scoring and the public find_email pipeline.

No test opens a socket: DNS and SMTP are fakes (this sandbox cannot reach port 25 anyway).
"""

from __future__ import annotations

import re
import smtplib
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import dns.resolver
import pytest
import respx

from pepite import enrich
from pepite.enrich import crawl, finder, verify
from pepite.enrich.providers import classify_mx, is_disposable, is_free_mail
from pepite.enrich.score import (
    VALID_PROBABILITY,
    assess,
    calibrate,
    combine,
    evidence_ratio,
)
from pepite.models import EmailStatus, Person, make_id

FIXTURES = Path(__file__).parent / "fixtures" / "enrich"
DOMAIN = "acme.example"
TARGET = "jane.doe@acme.example"
MX = [
    SimpleNamespace(preference=20, exchange="mx2.acme.example."),
    SimpleNamespace(preference=10, exchange="mx1.acme.example."),
]
M365 = [SimpleNamespace(preference=0, exchange="acme-example.mail.protection.outlook.com.")]
REJECT = (550, b"5.1.1 User unknown")
OK = (250, b"2.1.5 ok")
GREY = (451, b"4.7.1 Greylisted, please try again later")
RANDOM_RCPT = re.compile(r"^RCPT TO:<[a-z]+\.[a-z]+@acme\.example>$")


class FakeSMTP:
    """Records the SMTP dialogue. `replies` maps an address to one answer or to a list of
    answers given in turn (the last one repeats); `connect_errors` maps a host to an error."""

    replies: dict[str, object] = {}
    default: tuple[int, bytes] = REJECT
    connect_errors: dict[str, Exception] = {}
    connect_error: Exception | None = None
    sessions: list[FakeSMTP] = []
    asked: dict[str, int] = {}

    def __init__(self, host: str = "", port: int = 0, timeout: float | None = None) -> None:
        error = self.connect_errors.get(host) or self.connect_error
        if error:
            raise error
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
        if recip not in self.replies:
            return self.default
        answer = self.replies[recip]
        if isinstance(answer, list):
            n = self.asked.get(recip, 0)
            self.asked[recip] = n + 1
            return answer[min(n, len(answer) - 1)]
        return answer  # type: ignore[return-value]

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
        replies: dict[str, object] | None = None,
        default: tuple[int, bytes] = REJECT,
        connect_error: Exception | None = None,
        connect_errors: dict[str, Exception] | None = None,
    ) -> list[FakeSMTP]:
        sessions: list[FakeSMTP] = []
        attrs = {
            "replies": replies or {},
            "default": default,
            "connect_error": connect_error,
            "connect_errors": connect_errors or {},
            "sessions": sessions,
            "asked": {},
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
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """No real waiting: record the back-off and pacing sleeps instead."""
    waited: list[float] = []
    monkeypatch.setattr(verify, "_sleep", waited.append)
    monkeypatch.setenv("PEPITE_MODE", "local")
    verify.reset_rate_limits()
    return waited


@pytest.fixture(autouse=True)
def _fresh_crawl(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(crawl, "CRAWL_DELAY", 0.0)
    crawl.clear_cache()
    yield
    crawl.clear_cache()


# ---------------------------------------------------------------- providers


@pytest.mark.parametrize(
    ("hosts", "slug"),
    [
        (["aspmx.l.google.com", "alt1.aspmx.l.google.com"], "google"),
        (["smtp.google.com"], "google"),
        (["contoso-com.mail.protection.outlook.com"], "microsoft"),
        (["contoso-com.a-v1.mx.microsoft"], "microsoft"),
        (["mx.zoho.eu"], "zoho"),
        (["mail.protonmail.ch"], "proton"),
        (["mx1.mail.ovh.net"], "ovh"),
        (["mx00.ionos.de"], "ionos"),
        (["mx01.kundenserver.de"], "ionos"),
        (["spool.mail.gandi.net"], "gandi"),
        (["us-smtp-inbound-1.mimecast.com"], "gateway"),
        (["mx0a-001234.pphosted.com"], "gateway"),
        (["mail.acme.example"], "self"),
        (["mx.somehost.example"], "other"),
    ],
)
def test_classify_mx(hosts: list[str], slug: str) -> None:
    assert classify_mx(hosts, DOMAIN).slug == slug


def test_provider_trust_levels() -> None:
    assert classify_mx(["aspmx.l.google.com"]).rcpt == "limited"
    assert classify_mx(["x.mail.protection.outlook.com"]).rcpt == "limited"
    assert classify_mx(["eu-smtp-inbound-1.mimecast.com"]).rcpt == "hidden"
    assert classify_mx(["mx.zoho.com"]).rcpt == "reliable"


def test_free_and_disposable_domains() -> None:
    assert is_free_mail("gmail.com") and is_free_mail("gmx.de")
    assert not is_free_mail("acme.example")
    assert is_disposable("mailinator.com") and is_disposable("eu.mailinator.com")


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
        "provider": "self",
        "smtp": "skipped",
        "catch_all": "skipped",
        "role": "no",
        "disposable": "no",
        "free_mail": "no",
    }
    assert cand.confidence == 25  # score.DEFAULT_PRIOR: a lone address, not checked


def test_probe_valid_mailbox(dns_table, smtp) -> None:
    sessions = smtp({TARGET: OK})
    cand = enrich.verify_email(TARGET, smtp_probe=True)
    assert cand.status is EmailStatus.VALID
    assert (cand.checks["smtp"], cand.checks["catch_all"]) == ("ok", "no")
    assert cand.confidence >= 90
    (session,) = sessions
    assert (session.host, session.port) == ("mx1.acme.example", 25)
    assert session.timeout == verify.SMTP_TIMEOUT <= 10
    assert session.log[:3] == ["EHLO", "MAIL FROM:<>", f"RCPT TO:<{TARGET}>"]
    assert RANDOM_RCPT.match(session.log[3]) and TARGET not in session.log[3]  # human-looking
    assert session.log[4:] == ["QUIT"]


def test_probe_catch_all(dns_table, smtp) -> None:
    smtp(default=OK)
    cand = enrich.verify_email(TARGET, smtp_probe=True)
    assert cand.status is EmailStatus.CATCH_ALL
    assert cand.checks["catch_all"] == "yes"


@pytest.mark.parametrize(
    ("reply", "status", "smtp_check", "n_sessions"),
    [
        (REJECT, EmailStatus.INVALID, "fail", 1),
        ((554, b"5.7.1 Client host blocked using Spamhaus"), EmailStatus.UNKNOWN, "blocked", 1),
        (GREY, EmailStatus.UNKNOWN, "temp", 1 + len(verify.GREYLIST_DELAYS)),
    ],
)
def test_probe_rejections(dns_table, smtp, sleeps, reply, status, smtp_check, n_sessions) -> None:
    sessions = smtp({TARGET: reply})
    cand = enrich.verify_email(TARGET, smtp_probe=True)
    assert (cand.status, cand.checks["smtp"]) == (status, smtp_check)
    log = sessions[0].log
    assert log[:3] == ["EHLO", "MAIL FROM:<>", f"RCPT TO:<{TARGET}>"] and log[-1] == "QUIT"
    if smtp_check == "temp":  # the random address starts its greylisting timer too
        assert len(log) == 5 and RANDOM_RCPT.match(log[3])
    else:
        assert len(log) == 4
    assert len(sessions) == n_sessions  # 4xx is retried, 5xx and policy blocks are not
    if smtp_check == "temp":
        assert [s for s in sleeps if s >= 10] == list(verify.GREYLIST_DELAYS)


def test_greylisting_passes_on_retry(dns_table, smtp, sleeps) -> None:
    sessions = smtp({TARGET: [GREY, OK]})
    cand = enrich.verify_email(TARGET, smtp_probe=True)
    assert cand.status is EmailStatus.VALID
    assert len(sessions) == 2
    assert verify.GREYLIST_DELAYS[0] in sleeps


def test_throttled_catch_all_check_is_retried(dns_table, smtp) -> None:
    """One recipient per connection: the random check is asked again in a new session."""
    sessions = smtp({TARGET: OK}, default=REJECT)
    random_answers = [(451, b"4.7.500 Server busy"), REJECT]
    original = FakeSMTP.rcpt

    def rcpt(self, recip, options=()):
        if recip != TARGET:
            self.log.append(f"RCPT TO:<{recip}>")
            return random_answers.pop(0) if len(random_answers) > 1 else random_answers[0]
        return original(self, recip, options)

    smtplib.SMTP.rcpt = rcpt  # type: ignore[method-assign]
    cand = enrich.verify_email(TARGET, smtp_probe=True)
    assert cand.checks["catch_all"] == "no"
    assert cand.status is EmailStatus.VALID
    assert len(sessions) == 2
    assert not any(TARGET in line for line in sessions[1].log)  # only the random check


def test_probe_connection_error_tries_next_mx(dns_table, smtp) -> None:
    sessions = smtp(
        {TARGET: OK}, connect_errors={"mx1.acme.example": ConnectionRefusedError("down")}
    )
    cand = enrich.verify_email(TARGET, smtp_probe=True)
    assert cand.status is EmailStatus.VALID
    assert [s.host for s in sessions] == ["mx2.acme.example"]


def test_probe_unreachable(dns_table, smtp, sleeps) -> None:
    smtp(connect_error=ConnectionRefusedError("port 25 blocked"))
    cand = enrich.verify_email(TARGET, smtp_probe=True)
    assert cand.status is EmailStatus.UNKNOWN
    assert cand.checks["smtp"] == "error"
    assert not [s for s in sleeps if s >= 10]  # an unreachable host is not retried


def test_probe_uses_a_record_host(dns_table, smtp) -> None:
    dns_table[(DOMAIN, "MX")] = dns.resolver.NoAnswer()
    dns_table[(DOMAIN, "A")] = [SimpleNamespace(address="192.0.2.1")]
    sessions = smtp({TARGET: OK})
    cand = enrich.verify_email(TARGET, smtp_probe=True)
    assert cand.checks["provider"] == "self"
    assert cand.status is EmailStatus.VALID
    assert sessions[0].host == DOMAIN


def test_sessions_to_one_mx_are_paced(dns_table, smtp, sleeps) -> None:
    smtp({TARGET: OK})
    enrich.verify_email(TARGET, smtp_probe=True)
    assert sleeps == []
    enrich.verify_email(TARGET, smtp_probe=True)
    interval = classify_mx(["mx1.acme.example"], DOMAIN).min_interval
    assert len(sleeps) == 1 and 0 < sleeps[0] <= interval


def test_never_probes_in_demo_mode(dns_table, smtp, monkeypatch) -> None:
    sessions = smtp({TARGET: OK})
    monkeypatch.setenv("PEPITE_MODE", "demo")
    cand = enrich.verify_email(TARGET, smtp_probe=True)
    assert sessions == []
    assert cand.checks["smtp"] == "skipped"
    assert verify.probing_allowed(True) is False
    monkeypatch.setenv("PEPITE_MODE", "local")
    assert verify.probing_allowed(True) is True and verify.probing_allowed(False) is False


def test_microsoft_acceptance_alone_is_not_valid(dns_table, smtp) -> None:
    dns_table[(DOMAIN, "MX")] = M365
    smtp({TARGET: OK})
    cand = enrich.verify_email(TARGET, smtp_probe=True)
    assert cand.checks["provider"] == "microsoft"
    assert (cand.checks["smtp"], cand.checks["catch_all"]) == ("ok", "no")
    assert cand.status is EmailStatus.UNKNOWN
    assert cand.confidence < 90


def test_role_disposable_and_free_mail_flags(dns_table) -> None:
    dns_table[("mailinator.com", "MX")] = MX
    dns_table[("gmail.com", "MX")] = [SimpleNamespace(preference=5, exchange="gmail-smtp-in.l.")]
    cand = enrich.verify_email("info@mailinator.com")
    assert (cand.checks["role"], cand.checks["disposable"]) == ("yes", "yes")
    assert cand.confidence <= 5
    gmail = enrich.verify_email("someone@gmail.com")
    assert gmail.checks["free_mail"] == "yes"


# ---------------------------------------------------------------- scoring


def test_evidence_ratio_by_provider_and_pattern() -> None:
    def ratio(provider: str, smtp_check: str, catch_all: str, pattern: str | None = None) -> float:
        checks = {"provider": provider, "smtp": smtp_check, "catch_all": catch_all}
        return evidence_ratio(checks, pattern)

    assert ratio("self", "ok", "no") > ratio("google", "ok", "no") > ratio("gateway", "ok", "no")
    assert ratio("self", "ok", "yes") == 1.0  # catch-all: says nothing
    assert ratio("self", "ok", "no", "{first}") < ratio("self", "ok", "no", "{first}.{last}")
    assert ratio("self", "fail", "no") < 0.1
    assert ratio("microsoft", "skipped", "skipped") == 1.0


def test_combine_is_a_normalised_posterior() -> None:
    post = combine([0.6, 0.2, 0.1], [0.02, 30.0, 1.0])
    assert post[1] > 0.9 > post[0]
    assert sum(post) < 1  # the rest is "none of our candidates"
    assert combine([0.5], [1.0], rest=0.5) == [0.5]


def test_calibration_is_monotone_and_bounded() -> None:
    xs = [i / 50 for i in range(51)]
    ys = [calibrate(x) for x in xs]
    assert ys == sorted(ys)
    assert 0 <= ys[0] <= ys[-1] <= 1


def test_assess_status_rules() -> None:
    ok = {"syntax": "ok", "mx": "ok", "provider": "self", "role": "no", "disposable": "no"}
    probed = ok | {"smtp": "ok", "catch_all": "no"}
    assert assess(0.97, probed)[0] is EmailStatus.VALID
    # The format alone is never enough for "valid", however strong.
    assert assess(0.97, ok | {"smtp": "skipped", "catch_all": "skipped"})[0] is EmailStatus.UNKNOWN
    assert assess(0.97, ok | {"smtp": "skipped"}, published=True)[0] is EmailStatus.VALID
    assert assess(0.97, ok | {"smtp": "ok", "catch_all": "yes"})[0] is EmailStatus.CATCH_ALL
    assert assess(0.97, ok | {"smtp": "fail"}) == (EmailStatus.INVALID, 0)
    assert assess(0.97, probed | {"role": "yes"})[0] is EmailStatus.UNKNOWN
    # Microsoft accepted it, but the format evidence alone is weak: stays under the bar.
    ms = probed | {"provider": "microsoft"}
    status, conf = assess(0.97, ms, pattern_only=0.3)
    assert status is EmailStatus.UNKNOWN and conf < 100 * VALID_PROBABILITY
    assert assess(0.97, ms, pattern_only=0.8)[0] is EmailStatus.VALID


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
    assert cand.status is EmailStatus.UNKNOWN  # no mailbox check: never "valid"
    assert 75 <= cand.confidence < 100 * VALID_PROBABILITY + 5
    assert "fits all 2 personal address(es)" in cand.checks["basis"]
    assert "not checked over SMTP" in cand.checks["basis"]
    assert sessions == []


def test_find_email_probe_stops_at_first_accepted(dns_table, smtp) -> None:
    sessions = smtp({"priya@acme.example": OK})
    with respx.mock(assert_all_called=False) as router:
        mock_acme(router)
        cand = enrich.find_email(priya(), DOMAIN, smtp_probe=True)
    assert cand is not None
    assert (cand.address, cand.pattern) == ("priya@acme.example", "{first}")
    assert cand.checks["smtp"] == "ok"
    (session,) = sessions  # one SMTP session for all candidates
    rcpts = [line for line in session.log if line.startswith("RCPT")]
    assert rcpts[:2] == ["RCPT TO:<priya.patel@acme.example>", "RCPT TO:<priya@acme.example>"]
    assert len(rcpts) == 3  # + the random catch-all check, never the third candidate
    assert RANDOM_RCPT.match(rcpts[2])


def test_find_email_valid_when_server_confirms(dns_table, smtp) -> None:
    smtp({"priya.patel@acme.example": OK})
    with respx.mock(assert_all_called=False) as router:
        mock_acme(router)
        cand = enrich.find_email(priya(), DOMAIN, smtp_probe=True)
    assert cand is not None and cand.address == "priya.patel@acme.example"
    assert cand.status is EmailStatus.VALID and cand.confidence >= 90
    assert "confirmed the mailbox" in cand.checks["basis"]


def test_find_email_microsoft_needs_format_evidence() -> None:
    """Microsoft 365 accepts both; only the address backed by the site's format is valid."""
    checks = {
        "syntax": "ok",
        "mx": "ok",
        "provider": "microsoft",
        "smtp": "ok",
        "catch_all": "no",
        "role": "no",
        "disposable": "no",
        "free_mail": "no",
    }

    def fake_verify(addresses, *, smtp_probe=False):
        from pepite.models import EmailCandidate

        return [EmailCandidate(a, EmailStatus.UNKNOWN, 0, checks=dict(checks)) for a in addresses]

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(finder, "verify_addresses", fake_verify)
        mp.setattr(finder, "probing_allowed", lambda requested: requested)
        bare = finder.find_email_from("Claire Martin", DOMAIN, [], smtp_probe=True)
        backed = finder.find_email_from(
            "Claire Martin",
            DOMAIN,
            ["jane.doe@acme.example", "robert.smith@acme.example", "ann.lee@acme.example"],
            ["Jane Doe", "Robert Smith", "Ann Lee"],
            smtp_probe=True,
        )
    assert bare is not None and bare.status is EmailStatus.UNKNOWN
    assert "Microsoft 365 accepted the mailbox" in bare.checks["basis"]
    assert backed is not None and backed.address == "claire.martin@acme.example"
    assert backed.status is EmailStatus.VALID


def test_find_email_none_without_mail_domain(dns_table) -> None:
    dns_table[(DOMAIN, "MX")] = dns.resolver.NXDOMAIN()
    with respx.mock(assert_all_called=False) as router:
        mock_acme(router)
        assert enrich.find_email(priya(), DOMAIN) is None


def test_find_email_never_guesses_free_mail_domains(dns_table) -> None:
    assert finder.find_email_from("Jane Doe", "gmail.com", []) is None
    assert finder.find_email_from("Jane Doe", "mailinator.com", []) is None


def test_find_email_without_crawlable_site_uses_priors(dns_table) -> None:
    with respx.mock(assert_all_called=False) as router:
        router.route().respond(404)
        cand = enrich.find_email(priya(), DOMAIN)
    assert cand is not None
    assert cand.address == "priya.patel@acme.example"
    assert cand.status is EmailStatus.UNKNOWN
    assert 25 <= cand.confidence <= 50  # the most common format worldwide, nothing more
    assert "most common format" in cand.checks["basis"]
