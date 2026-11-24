from __future__ import annotations

import csv
from pathlib import Path

import pytest

from pepite import enrich, pipeline
from pepite.models import EmailCandidate, EmailStatus
from pepite.search import CompanyFilters
from tests.fakes import FakeSearch, FakeStore
from tests.test_api import make_settings


@pytest.fixture
def calls(monkeypatch):
    log: list[tuple[str, str]] = []

    def fake(person, domain, *, smtp_probe=False):
        log.append((person.full_name, domain))
        if person.full_name.startswith("Nobody"):
            return None
        first, _, last = person.full_name.lower().partition(" ")
        return EmailCandidate(f"{first}.{last}@{domain}", EmailStatus.VALID, 85, "{first}.{last}")

    monkeypatch.setattr(enrich, "find_email", fake)
    return log


def run(tmp_path: Path, content: str | bytes, *, store=None, search=None, progress=None):
    src = tmp_path / "in.csv"
    src.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
    out = tmp_path / "out.csv"
    res = pipeline.enrich_csv(
        src,
        out,
        store=store or FakeStore(),
        search=search or FakeSearch(),
        settings=make_settings(tmp_path),
        progress=progress,
    )
    with out.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    return res, rows


def test_basic_with_aliases_and_extra_columns(tmp_path: Path, calls) -> None:
    content = "Prénom,Nom,Site,Notes perso\nClaire,Martin,https://www.Example.com/about,vip\n"
    res, rows = run(tmp_path, content)
    assert res["rows"] == 1 and res["found"] == 1 and res["output"] == str(tmp_path / "out.csv")
    r = rows[0]
    assert r["Notes perso"] == "vip"
    assert r["email"] == "claire.martin@example.com"
    assert r["email_status"] == "valid" and r["email_confidence"] == "85"
    assert r["email_pattern"] == "{first}.{last}"
    assert r["company_domain_resolved"] == "example.com"
    assert calls == [("Claire Martin", "example.com")]
    assert list(r)[-6:] == list(pipeline.OUTPUT_COLUMNS)


@pytest.mark.parametrize("delim", [",", ";", "\t"])
def test_delimiters_and_bom(tmp_path: Path, calls, delim: str) -> None:
    content = (
        delim.join(["FirstName", "LastName", "Domain"])
        + "\n"
        + delim.join(["A", "B", "x.example"])
        + "\n"
    )
    res, rows = run(tmp_path, b"\xef\xbb\xbf" + content.encode())
    assert res["found"] == 1
    assert rows[0]["email"] == "a.b@x.example"


def test_full_name_alias_case_insensitive(tmp_path: Path, calls) -> None:
    res, rows = run(tmp_path, "NOM COMPLET;SITE\nJean Dupont;x.example\n")
    assert rows[0]["email"] == "jean.dupont@x.example"


def test_domain_resolution_from_company_name(tmp_path: Path, calls) -> None:
    seen: list[CompanyFilters] = []
    search = FakeSearch()
    orig = search.search_companies

    def spy(filters, page=1, per_page=25):
        seen.append(filters)
        return orig(filters, page=page, per_page=per_page)

    search.search_companies = spy  # type: ignore[method-assign]
    content = (
        "first_name,last_name,entreprise\n"
        "A,One,Nordwind Data\nB,Two,nordwind data\nC,Three,Unknown Corp\n"
    )
    res, rows = run(tmp_path, content, search=search)
    assert rows[0]["company_domain_resolved"] == "nordwind.example"
    assert rows[0]["email"] == "a.one@nordwind.example"
    assert rows[1]["email"] == "b.two@nordwind.example"
    assert rows[2]["email"] == "" and rows[2]["notes"] == "no domain"
    assert len(seen) == 2  # resolved once per distinct company
    assert res["found"] == 2


def test_suppressed_and_not_found(tmp_path: Path, calls) -> None:
    store = FakeStore()
    store.add_suppression("blocked.example")
    content = (
        "name,domain\nClaire Martin,blocked.example\nNobody Here,x.example\n"
        ",x.example\nOk Person,x.example\n"
    )
    res, rows = run(tmp_path, content, store=store)
    assert rows[0]["email"] == "" and rows[0]["notes"] == "suppressed"
    assert rows[1]["notes"] == "no email found"
    assert rows[2]["notes"] == "missing name"
    assert rows[3]["email"] == "ok.person@x.example"
    assert res == {"rows": 4, "found": 1, "output": str(tmp_path / "out.csv")}


def test_repeated_person_domain_cached(tmp_path: Path, calls) -> None:
    run(tmp_path, "name,domain\nA B,x.example\nA B,x.example\n")
    assert len(calls) == 1


def test_row_error_does_not_abort(tmp_path: Path, monkeypatch) -> None:
    def fake(person, domain, *, smtp_probe=False):
        if person.full_name == "Bad Row":
            raise RuntimeError("x")
        return EmailCandidate("g@x.example", EmailStatus.UNKNOWN, 10)

    monkeypatch.setattr(enrich, "find_email", fake)
    res, rows = run(tmp_path, "name,domain\nBad Row,x.example\nGood Row,x.example\n")
    assert rows[0]["notes"] == "error: RuntimeError"
    assert rows[1]["email"] == "g@x.example" and rows[1]["email_pattern"] == ""


def test_max_rows(tmp_path: Path, calls, monkeypatch) -> None:
    monkeypatch.setattr(pipeline, "MAX_ROWS", 3)
    run(tmp_path, "name,domain\n" + "A B,x.example\n" * 3)
    with pytest.raises(ValueError, match="Too many rows"):
        run(tmp_path, "name,domain\n" + "A B,x.example\n" * 4)
    assert pipeline.MAX_ROWS == 3


def test_missing_columns(tmp_path: Path, calls) -> None:
    with pytest.raises(ValueError):
        run(tmp_path, "foo,bar\n1,2\n")
    with pytest.raises(ValueError):
        run(tmp_path, "name,foo\n1,2\n")


def test_progress_and_atomic_output(tmp_path: Path, calls) -> None:
    seen: list[tuple[int, int]] = []
    run(
        tmp_path,
        "name,domain\nA B,x.example\nC D,y.example\n",
        progress=lambda i, n: seen.append((i, n)),
    )
    assert seen == [(1, 2), (2, 2)]
    assert not list(tmp_path.glob("*.tmp"))


def test_normalize_domain() -> None:
    n = pipeline.normalize_domain
    assert n("HTTPS://www.Foo.com:8080/x?y") == "foo.com"
    assert n("foo.co.uk/path") == "foo.co.uk"
    assert n("me@foo.com") == "foo.com"
    assert n("") is None and n(None) is None and n("notadomain") is None
