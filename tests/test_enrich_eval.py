"""Email evaluation harness: the gold fixture, leave-one-out, simulation, report, gold builder.

The gold fixture (tests/fixtures/email_gold, made by tools/make_email_gold.py) holds fake people
at fake companies under .example. dev.csv tuned the model and fitted the calibration; test.csv
was held out. docs/email-quality.md reports the numbers and the baseline.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest
import respx
from typer.testing import CliRunner

from leadcommons.cli import app
from leadcommons.enrich import crawl, verify
from leadcommons.enrich import eval as ev
from leadcommons.enrich.crawl import Page

GOLD = Path(__file__).parent / "fixtures" / "email_gold"
FIXTURES = Path(__file__).parent / "fixtures" / "enrich"
runner = CliRunner()


@pytest.fixture(autouse=True)
def _local_mode(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LEADCOMMONS_MODE", "local")
    monkeypatch.setattr(crawl, "CRAWL_DELAY", 0.0)
    crawl.clear_cache()
    yield
    crawl.clear_cache()


def run(split: str, smtp: bool) -> ev.Report:
    gold = GOLD / f"{split}.csv"
    return ev.evaluate(ev.load_gold(gold), world=ev.load_world(ev.world_path_for(gold)), smtp=smtp)


# ---------------------------------------------------------------- the gold fixture


@pytest.mark.parametrize("split", ["test", "dev"])
def test_valid_means_at_least_90_percent_right_with_smtp(split: str) -> None:
    report = run(split, smtp=True)
    o = report.overall
    assert report.mode == {
        "site evidence": "replayed from world file",
        "dns": "simulated",
        "smtp": "simulated",
    }
    assert o.valid_precision is not None and o.valid_precision >= 0.9
    assert o.valid_coverage is not None and o.valid_coverage >= 0.5
    assert report.valid_interval is not None and report.valid_interval[0] >= 0.9
    assert o.top_precision is not None and o.top_precision >= 0.75
    assert report.ece is not None and report.ece <= 5


@pytest.mark.parametrize("split", ["test", "dev"])
def test_without_smtp_nothing_unverified_is_called_valid(split: str) -> None:
    report = run(split, smtp=False)
    o = report.overall
    assert o.valid_n == 0 or (o.valid_precision or 0) >= 0.9
    assert o.top_precision is not None and o.top_precision >= 0.75  # baseline: 66-70%
    assert o.top_coverage == 1.0
    assert o.recall_at_3 is not None and o.recall_at_3 >= 0.85
    assert report.ece is not None and report.ece <= 8
    assert set(report.by_provider) >= {"google", "microsoft", "gateway"}


def test_calibration_fit_is_monotone() -> None:
    knots = ev.fit_calibration(run("dev", smtp=False).rows)
    assert knots is not None and len(knots) >= 3
    xs, ys = zip(*knots, strict=True)
    assert list(xs) == sorted(xs) and list(ys) == sorted(ys)
    assert all(0 <= y <= 1 for y in ys)


# ---------------------------------------------------------------- harness mechanics


def write_world(tmp_path: Path, domains: dict, rows: list[tuple[str, str, str]]) -> Path:
    gold = tmp_path / "gold.csv"
    with gold.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["full_name", "domain", "true_address", "source"])
        writer.writerows([*r, "https://example/team"] for r in rows)
    ev.world_path_for(gold).write_text(json.dumps({"domains": domains}), encoding="utf-8")
    return gold


def test_own_address_is_held_out(tmp_path: Path) -> None:
    domain = "quux.example"
    gold = write_world(
        tmp_path,
        {
            domain: {
                "site_names": ["Zed Quux", "Anna Berg"],
                "site_emails": [f"zq77@{domain}", f"anna.berg@{domain}", f"info@{domain}"],
                "mx": [f"mail.{domain}"],
                "smtp": "reject_unknown",
                "mailboxes": ["zq77", "anna.berg", "info"],
            }
        },
        [("Zed Quux", domain, f"zq77@{domain}")],
    )
    report = ev.evaluate(ev.load_gold(gold), world=ev.load_world(ev.world_path_for(gold)))
    (row,) = report.rows
    assert row.evidence == 1  # anna.berg only: the person's own address and info@ do not count
    assert row.top_guess == f"zed.quux@{domain}"
    assert not row.top_correct and row.true_rank is None
    assert row.status == "unknown"


def test_simulated_greylisting_is_retried(tmp_path: Path) -> None:
    domain = "grey.example"
    gold = write_world(
        tmp_path,
        {
            domain: {
                "site_names": ["Anna Berg", "Omar Haddad", "Eva Lind"],
                "site_emails": [f"anna.berg@{domain}", f"omar.haddad@{domain}"],
                "mx": [f"mail.{domain}"],
                "smtp": "greylist",
                "mailboxes": ["anna.berg", "omar.haddad", "eva.lind"],
                "provider": "self",
            }
        },
        [("Eva Lind", domain, f"eva.lind@{domain}")],
    )
    world = ev.load_world(ev.world_path_for(gold))
    with ev.simulated_network(world, smtp=True) as server:
        cand = verify.verify_addresses([f"eva.lind@{domain}"], smtp_probe=True)[0]
    assert cand.checks["smtp"] == "ok" and server.sessions == 2
    report = ev.evaluate(ev.load_gold(gold), world=world, smtp=True)
    assert report.rows[0].valid and report.rows[0].shown_correct


def test_report_text_json_and_details(tmp_path: Path) -> None:
    report = run("test", smtp=True)
    text = ev.format_report(report)
    for part in ("Top-ranked guess", "Shown as valid", "Calibration", "By mail provider"):
        assert part in text
    json.dumps(report.to_dict())
    path = tmp_path / "details.csv"
    ev.write_details(report, path)
    with path.open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == report.overall.n and "basis" in rows[0]


def test_load_gold_needs_the_three_columns(tmp_path: Path) -> None:
    path = tmp_path / "bad.csv"
    path.write_text("name,domain\nJane Doe,acme.example\n", encoding="utf-8")
    with pytest.raises(ValueError, match="true_address"):
        ev.load_gold(path)


# ---------------------------------------------------------------- gold builder


def test_pairs_names_with_addresses_published_beside_them() -> None:
    page = Page("https://acme.example/team", (FIXTURES / "team_emails.html").read_text())
    names = ["Jane Doe", "Robert Smith", "Priya Patel", "Mark Jones", "Tom Baker", "Lucy Gray"]
    pairs = ev.pair_names_and_addresses([page], "acme.example", [*names, "Anna Berg"])
    got = {(p.full_name, p.true_address) for p in pairs}
    assert got == {
        ("Jane Doe", "jane.doe@acme.example"),
        ("Robert Smith", "r.smith@acme.example"),  # any format, not only the common ones
        ("Anna Berg", "anna.berg@acme.example"),  # name and obfuscated address on two lines
    }
    # Not paired: info@ (shared), the assistant's address next to Mark Jones, and tbaker@ in a
    # block that names two people.
    assert all(p.source_url == "https://acme.example/team" for p in pairs)


def test_build_gold_cli(tmp_path: Path) -> None:
    domains = tmp_path / "domains.txt"
    domains.write_text("# companies to sample\nacme.example\n", encoding="utf-8")
    out = tmp_path / "gold.csv"
    with respx.mock(assert_all_called=False) as router:
        router.get("https://acme.example/robots.txt").respond(404)
        router.get("https://acme.example/").respond(200, html='<a href="/team">Team</a>')
        router.get("https://acme.example/team").respond(
            200, html=(FIXTURES / "team_emails.html").read_text()
        )
        result = runner.invoke(app, ["eval", "build-gold", str(domains), "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert "personal data" in result.output
    rows = ev.load_gold(out)
    assert ("Jane Doe", "jane.doe@acme.example") in {(r.full_name, r.true_address) for r in rows}
    world = ev.load_world(ev.world_path_for(out))
    assert "jane.doe@acme.example" in world["acme.example"].site_emails
    assert world["acme.example"].mx is None  # real gold: DNS and SMTP stay live


# ---------------------------------------------------------------- command line


def test_eval_emails_cli(tmp_path: Path) -> None:
    gold = str(GOLD / "test.csv")
    out = tmp_path / "report.json"
    result = runner.invoke(
        app,
        ["eval", "emails", "--gold", gold, "--smtp", "--min-valid-precision", "0.9"]
        + ["--json", str(out)],
    )
    assert result.exit_code == 0, result.output
    assert "Shown as valid" in result.output and "smtp: simulated" in result.output
    assert json.loads(out.read_text())["overall"]["valid_precision"] >= 0.9
    strict = runner.invoke(
        app,
        ["eval", "emails", "--gold", gold, "--smtp"]
        + [
            "--min-valid-precision",
            "0.999",
        ],
    )
    assert strict.exit_code == 1
