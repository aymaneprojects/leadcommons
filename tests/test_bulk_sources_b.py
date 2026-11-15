"""Bulk register sources B: Brønnøysund (Norway), Australian Business Register, Corporations
Canada and the Wikidata organisations bridge, against fixtures that copy the real formats.

The Wikidata fixtures (tests/fixtures/bulk/wikidata/) are real answers of query.wikidata.org
recorded in November 2026; the register fixtures copy the real headers / elements with fake rows.
"""

from __future__ import annotations

import gzip
import json
import re
import zipfile
from pathlib import Path
from urllib.parse import parse_qs

import duckdb
import httpx
import pytest
from typer.testing import CliRunner

from leadcommons.cli import app
from leadcommons.connectors.bulk import (
    SOURCES,
    BuildOptions,
    _rules,
    abr,
    brreg,
    canada_corps,
    load_all,
    wikidata_orgs,
)
from leadcommons.models import make_id
from leadcommons.packs import bulk
from leadcommons.packs.normalize import size_bucket
from leadcommons.search import PACK_COLUMNS

FIX = Path(__file__).parent / "fixtures" / "bulk"


def rows(path: Path) -> list[dict]:
    con = duckdb.connect()
    cur = con.execute(f"SELECT * FROM read_parquet('{path}')")
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r, strict=True)) for r in cur.fetchall()]


def by(items: list[dict], key: str) -> dict[str, dict]:
    return {r[key]: r for r in items}


def opts(tmp_path: Path, **kw) -> BuildOptions:
    return BuildOptions(work_dir=tmp_path / "work", out_dir=tmp_path / "shards", **kw)


def con_with_macros():
    con = duckdb.connect()
    bulk.install_macros(con)
    return con


def sql_value(sql: str, value):
    """Evaluate a SQL expression over `$1`."""
    return con_with_macros().execute(f"SELECT {sql}", [value]).fetchone()[0]


# --------------------------------------------------------------------------- registry, rules


def test_the_four_sources_are_registered() -> None:
    load_all()
    expected = {
        "brreg": ("NLOD-2.0", {"NO"}),
        "abr": ("CC-BY-3.0-AU", {"AU"}),
        "canada_corps": ("OGL-Canada-2.0", {"CA"}),
        "wikidata_orgs": ("CC0-1.0", None),
    }
    for name, (licence, countries) in expected.items():
        src = SOURCES[name]
        assert src.licence == licence and src.countries == countries
        assert src.attribution and src.licence_url.startswith("https://")
    assert "CC BY 3.0 AU" in SOURCES["abr"].attribution
    listed = json.loads(CliRunner().invoke(app, ["pack", "sources", "--json"]).output)
    assert {"brreg", "abr", "canada_corps", "wikidata_orgs"} <= {s["name"] for s in listed}


@pytest.mark.parametrize(
    ("name", "kept"),
    [
        ("NORDMANN RØRLEGGERSERVICE", True),
        ("NORDMANN BYGG", True),
        ("FJELLTOPP DESIGN", True),
        ("OLA NORDMANN", False),
        ("FOTOGRAF KARI NORDMANN", False),
        ("K. HANSEN TRANSPORT", False),
        ("NORDMANN", False),
    ],
)
def test_sole_proprietors_keep_trade_names_only(name: str, kept: bool) -> None:
    assert sql_value(_rules.trade_name_sql("$1"), name) is kept


@pytest.mark.parametrize(
    ("name", "personal"),
    [
        ("PER HANSEN OG KARI HANSEN", True),
        ("J P CITIZEN & M A CITIZEN", True),
        ("JOHN SMITH & MARY SMITH", True),
        ("SMITH & JONES", False),
        ("J & M CITIZEN PLUMBING", False),
        ("THE TRUSTEE FOR SMITH FAMILY TRUST", False),
        ("STORBYEN IDRETTSLAG", False),
        (None, False),
    ],
)
def test_names_of_persons_are_recognised(name: str | None, personal: bool) -> None:
    assert sql_value(_rules.personal_name_sql("CAST($1 AS VARCHAR)"), name) is personal


def test_size_ranges_match_size_bucket() -> None:
    con = con_with_macros()
    for n in [None, -3, 0, 1, 10, 11, 50, 51, 200, 201, 500, 999, 1000, 1001, 5000, 9999, 10001]:
        got = con.execute(f"SELECT {_rules.size_range_sql('CAST($1 AS INTEGER)')}", [n]).fetchone()[
            0
        ]
        assert got == size_bucket(n), n


@pytest.mark.parametrize(
    ("url", "domain", "website"),
    [
        ("www.fjordkraft-test.no", "fjordkraft-test.no", "http://www.fjordkraft-test.no"),
        ("https://Shop.Example.co.uk/x", "example.co.uk", "https://Shop.Example.co.uk/x"),
        ("www.facebook.com/storbyenil", None, "http://www.facebook.com/storbyenil"),
        ("https://acme.wixsite.com/home", None, "https://acme.wixsite.com/home"),
        ("https://www.facebook.com/", "facebook.com", "https://www.facebook.com/"),
        ("https://www.lunner.kommune.no/", None, "https://www.lunner.kommune.no/"),
        ("ikke oppgitt", None, None),
        (None, None, None),
    ],
)
def test_domains_skip_profile_pages_on_shared_hosts(url, domain, website) -> None:
    assert sql_value(_rules.domain_sql("CAST($1 AS VARCHAR)"), url) == domain
    assert sql_value(_rules.website_sql("CAST($1 AS VARCHAR)"), url) == website


def test_user_agent_declares_the_contact() -> None:
    assert _rules.user_agent("ops@example.org").endswith("; contact: ops@example.org)")
    assert _rules.user_agent().startswith("LeadCommons/")


# --------------------------------------------------------------------------- Brønnøysund


@pytest.fixture
def brreg_rows(tmp_path: Path, monkeypatch) -> list[dict]:
    monkeypatch.setenv(brreg.URL_ENV, str(FIX / "brreg_enheter.json"))
    stats = brreg.build(opts(tmp_path))
    assert stats.files == [tmp_path / "shards" / "brreg.parquet"]
    assert stats.rows == 6 and stats.skipped == 7
    assert "2 sole proprietorship named after a person" in stats.notes
    assert "1 bankrupt" in stats.notes and "1 estate" in stats.notes
    return rows(stats.files[0])


def test_brreg_keeps_active_companies_and_trade_names(brreg_rows: list[dict]) -> None:
    names = {r["name"] for r in brreg_rows}
    assert names == {
        "FJORDKRAFT TESTSELSKAP ASA",
        "NORDLYS DATA AS",
        "NORDMANN RØRLEGGERSERVICE",
        "STORBYEN IDRETTSLAG",
        "SVENSK FILIAL NUF",
        "HAVBRUK VEST AS",
    }
    text = json.dumps(brreg_rows, default=str)
    assert "OLA NORDMANN" not in text and "KARI" not in text  # no natural person's name


def test_brreg_fields(brreg_rows: list[dict]) -> None:
    assert list(brreg_rows[0]) == list(PACK_COLUMNS)
    got = by(brreg_rows, "company_number")
    asa = got["999100001"]
    assert asa["id"] == make_id("co", "reg", "NO-BRREG:999100001")
    assert (asa["registry"], asa["country"], asa["status"]) == ("NO-BRREG", "NO", "active")
    assert (
        asa["domain"] == "fjordkraft-test.no" and asa["website"] == "http://www.fjordkraft-test.no"
    )
    assert (asa["region"], asa["city"], asa["postal_code"]) == ("Vestland", "Bergen", "5003")
    assert asa["address"] == "Testveien 1, Bygg B"  # business address, not the PO box
    assert asa["industry"] == "D" and asa["industry_label"].startswith("Electricity")
    assert (asa["employees"], asa["size_range"], asa["founded_year"]) == (812, "501-1000", 1998)
    assert asa["legal_form"] == "Allmennaksjeselskap"
    assert json.loads(asa["sources_json"])[0]["licence"] == "NLOD-2.0"
    enk = got["999100007"]
    assert enk["founded_year"] == 2011 and enk["region"] == "Innlandet"  # registration date
    club = got["999100010"]
    assert club["domain"] is None and club["website"].endswith("facebook.com/storbyenil")
    assert club["founded_year"] is None  # registered in 1995, when the register opened
    assert club["address"] == "Postboks 55" and club["city"] == "Fredrikstad"  # postal address
    nuf = got["999100012"]
    assert nuf["country"] == "NO" and nuf["postal_code"] is None and nuf["city"] is None
    fish = got["999100013"]
    assert fish["employees"] is None and fish["size_range"] is None and fish["industry"] == "A"


def test_brreg_reads_the_gzipped_download(tmp_path: Path, monkeypatch) -> None:
    gz = tmp_path / "enheter_alle.json.gz"
    gz.write_bytes(gzip.compress((FIX / "brreg_enheter.json").read_bytes()))
    monkeypatch.setenv(brreg.URL_ENV, str(gz))
    stats = brreg.build(opts(tmp_path))
    assert stats.rows == 6 and brreg.is_gzip(gz)


def test_brreg_limit_and_country_skip(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(brreg.URL_ENV, str(FIX / "brreg_enheter.json"))
    first = rows(brreg.build(opts(tmp_path, limit=2)).files[0])
    again = rows(brreg.build(opts(tmp_path / "b", limit=2)).files[0])
    assert len(first) == 2 and [r["id"] for r in first] == [r["id"] for r in again]
    stats = bulk.run_source("brreg", opts(tmp_path / "c", countries=frozenset({"se"})))
    assert stats.rows == 0 and not stats.files


# --------------------------------------------------------------------------- ABR


def abr_zip(tmp_path: Path) -> Path:
    path = tmp_path / "public_split_1_10.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for xml in sorted((FIX / "abr").glob("*.xml")):
            zf.write(xml, xml.name)
    return path


@pytest.fixture
def abr_rows(tmp_path: Path, monkeypatch) -> list[dict]:
    monkeypatch.setenv(abr.URL_ENV, str(abr_zip(tmp_path)))
    stats = abr.build(opts(tmp_path))
    assert stats.rows == 7 and stats.skipped == 7
    assert "2 cancelled ABNs or individuals skipped" in stats.notes
    for why in ("self-managed super fund", "deceased estate", "family investment trust",
                "named after persons", "replaced ABN"):  # fmt: skip
        assert f"1 {why}" in stats.notes
    return rows(stats.files[0])


def test_abr_keeps_active_organisations(abr_rows: list[dict]) -> None:
    got = by(abr_rows, "company_number")
    assert set(got) == {"51900000001", "62900000002", "95900000005", "27900000007",
                        "72900000012", "83900000013", "94900000014"}  # fmt: skip
    text = json.dumps(abr_rows)
    assert "CITIZEN &" not in text and "JOHN" not in text and "GORDON" not in text


def test_abr_fields(abr_rows: list[dict]) -> None:
    got = by(abr_rows, "company_number")
    pub = got["51900000001"]
    assert pub["id"] == make_id("co", "reg", "AU-ABN:51900000001")
    assert (pub["name"], pub["legal_form"]) == (
        "SOUTHERN CROSS TEST MINING LIMITED",
        "Australian Public Company",
    )
    assert (pub["region"], pub["postal_code"], pub["country"]) == (
        "Western Australia",
        "6000",
        "AU",
    )
    assert pub["founded_year"] is None  # ABN of 1999: issued to every business then
    assert got["62900000002"]["founded_year"] == 2015
    family = got["95900000005"]  # partnership named after its partners: business name shown
    assert family["name"] == "CITIZEN PLUMBING" and family["legal_name"] is None
    trust = got["27900000007"]
    assert trust["name"] == "SUNSHINE COAST CAFE"
    assert trust["legal_name"] == "THE TRUSTEE FOR THE CITIZEN FAMILY TRUST"
    assert got["83900000013"]["region"] == "Victoria"  # "vic" in the file
    assert json.loads(pub["sources_json"])[0]["licence"] == "CC-BY-3.0-AU"


def test_abr_resumes_parsed_files(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(abr.URL_ENV, str(abr_zip(tmp_path)))
    abr.build(opts(tmp_path))
    raw = tmp_path / "work" / "raw"
    assert sorted(p.name for p in raw.glob("*.parquet")) == [
        "20261111_Public01.parquet",
        "20261111_Public02.parquet",
    ]
    monkeypatch.setattr(abr, "parse_to_parquet", lambda *a, **k: pytest.fail("parsed again"))
    stats = abr.build(opts(tmp_path))
    assert stats.rows == 7 and stats.skipped == 7


def test_abr_parser_batches_and_frees_memory(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(abr, "BATCH_ROWS", 2)
    out = tmp_path / "p.parquet"
    with (FIX / "abr" / "20261111_Public02.xml").open("rb") as fh:
        assert abr.parse_to_parquet(fh, out, label="t") == (7, 7)
    import pyarrow.parquet as pq

    assert pq.ParquetFile(out).metadata.num_row_groups == 4
    xml = (FIX / "abr" / "20261111_Public01.xml").read_bytes()
    namespaced = xml.replace(b"<Transfer>", b'<Transfer xmlns="urn:example:abr">')
    import io

    recs = [r for r in abr.iter_records(io.BytesIO(namespaced)) if r is not None]
    assert [r[0] for r in recs] == ["51900000001", "62900000002", "95900000005",
                                    "16900000006", "27900000007"]  # fmt: skip
    assert recs[0][8] == "SOUTHERN CROSS MINING"  # trading name kept as the business name


def test_abr_finds_the_zip_files_in_the_dataset_api() -> None:
    package = {
        "result": {
            "resources": [
                {"name": "ABN Bulk Extract readme", "url": "https://x/readme.docx"},
                {"name": "Public split 11-20", "url": "https://x/public_split_11_20.zip"},
                {"name": "Public split 1-10", "url": "https://x/public_split_1_10.zip"},
                {"name": "XML schema", "url": "https://x/schema.zip"},
            ]
        }
    }
    assert abr.zip_urls(package) == [
        "https://x/public_split_1_10.zip",
        "https://x/public_split_11_20.zip",
    ]


# --------------------------------------------------------------------------- Corporations Canada


@pytest.fixture
def ca_rows(tmp_path: Path, monkeypatch) -> list[dict]:
    files = [FIX / "corporations-active-cbca-en.csv", FIX / "corporations-active-other-en.csv"]
    monkeypatch.setenv(canada_corps.URL_ENV, " ".join(str(f) for f in files))
    stats = canada_corps.build(opts(tmp_path))
    assert stats.rows == 6 and stats.skipped == 4
    assert "2 dissolution pending" in stats.notes and "1 not active" in stats.notes
    return rows(stats.files[0])


def test_canada_keeps_active_corporations(ca_rows: list[dict]) -> None:
    got = by(ca_rows, "company_number")
    assert set(got) == {"11900001", "11900002", "11900005", "04360021", "4590001", "4590002"}


def test_canada_fields(ca_rows: list[dict]) -> None:
    got = by(ca_rows, "company_number")
    maple = got["11900001"]
    assert maple["id"] == make_id("co", "reg", "CA-CORP:11900001")
    assert (maple["name"], maple["registry"], maple["country"]) == (
        "MAPLE TEST SOFTWARE INC.",
        "CA-CORP",
        "CA",
    )
    assert (maple["region"], maple["city"], maple["postal_code"]) == (
        "Ontario",
        "Ottawa",
        "K2P 2L8",
    )
    assert maple["address"] == "100 Example Street, Suite 200" and maple["founded_year"] == 2018
    assert maple["legal_form"] == "Canada Business Corporations Act"
    quebec = got["11900002"]  # French form 1, English form 2: the English name is shown
    assert quebec["name"] == "Quebec Test Company Ltd." and quebec["postal_code"] == "H3B 4W8"
    assert quebec["city"] == "Montréal"
    toronto = got["11900005"]
    assert toronto["city"] == "Toronto" and toronto["postal_code"] == "M5V 3L9"
    assert got["04360021"]["name"] == "PACIFIC SAMPLE HOLDINGS LTD."  # "0436002-1" as written
    assert got["4590001"]["legal_form"] == "Canada Not-for-profit Corporations Act"


def test_canada_reads_files_with_their_own_header(tmp_path: Path, monkeypatch) -> None:
    short = tmp_path / "corporations-active-x-en.csv"
    short.write_text(
        "Status,Corporate name - form 1,Corporation number,City/town,Province/territory\n"
        "Active,SHORT HEADER TEST INC.,1234567-8,Iqaluit,NU\n",
        encoding="utf-8",
    )
    monkeypatch.setenv(canada_corps.URL_ENV, f"{FIX / 'corporations-active-cbca-en.csv'} {short}")
    got = by(rows(canada_corps.build(opts(tmp_path)).files[0]), "company_number")
    assert got["12345678"]["region"] == "Nunavut" and got["12345678"]["postal_code"] is None


def test_canada_picks_the_english_active_files(monkeypatch) -> None:
    base = "https://d4bf66bykfyaf.cloudfront.net/"
    names = ["corporations-inactive-cbca-en.csv", "corporations-active-other-en.csv",
             "corporations-active-cbca-fr.csv", "corporations-active-cbca-en.csv"]  # fmt: skip
    package = {"result": {"resources": [{"url": base + n} for n in names]}}
    assert canada_corps.csv_urls(package) == [
        base + "corporations-active-cbca-en.csv",
        base + "corporations-active-other-en.csv",
    ]

    def down(*a, **k):
        raise RuntimeError("GET failed")

    monkeypatch.delenv(canada_corps.URL_ENV, raising=False)
    monkeypatch.setattr(bulk, "get_text", down)
    assert canada_corps.resolve_urls() == list(canada_corps.FALLBACK_URLS)


# --------------------------------------------------------------------------- Wikidata bridge


IDS = json.loads((FIX / "wikidata" / "ids.json").read_text(encoding="utf-8"))
DETAILS = json.loads((FIX / "wikidata" / "details.json").read_text(encoding="utf-8"))
# Wikidata property -> (recorded id listing, details field). A fake listing holds the recorded
# listing (LIMIT 3) plus every recorded item that has the property, as the real one would.
PROPERTIES = {"P2622": ("gb_ch", "gb_ch"), "P1616": ("siren_site", "siren"),
              "P3548": ("abn", "abn"), "P13850": ("ca_corp", "ca_corp"), "P5531": ("cik", "cik"),
              "P2333": ("no_orgnr", "no_orgnr"), "P3549": ("acn", "acn"),
              "P1278": (None, "lei")}  # fmt: skip


def listing(prop: str) -> list[dict]:
    name, column = PROPERTIES[prop]
    items = [b["item"]["value"] for b in IDS[name]["results"]["bindings"]] if name else []
    items += [b["item"]["value"] for b in DETAILS["results"]["bindings"] if column in b]
    return [{"item": {"type": "uri", "value": v}} for v in dict.fromkeys(items)]


class FakeWdqs:
    """Answers the bridge's queries from the recorded query.wikidata.org responses."""

    def __init__(self, *, fail: callable = lambda query: False) -> None:
        self.queries: list[str] = []
        self.fail = fail
        self.agents: set[str] = set()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        query = parse_qs(request.content.decode())["query"][0]
        self.queries.append(query)
        self.agents.add(request.headers["User-Agent"])
        if self.fail(query):
            return httpx.Response(200, text='{"head": {"vars": ["item"]}, "results": {"bindi')
        if "wdt:P297 ?code" in query:  # ISO code -> country item
            codes = re.findall(r'"([A-Z]{2})"', query)
            items = {"NO": "Q20", "AU": "Q408", "FR": "Q142", "GB": "Q145", "US": "Q30"}
            bindings = [
                {"country": {"type": "uri", "value": f"http://www.wikidata.org/entity/{items[c]}"},
                 "code": {"type": "literal", "value": c}}
                for c in codes if c in items
            ]  # fmt: skip
            return httpx.Response(200, json={"results": {"bindings": bindings}})
        if query.startswith("SELECT DISTINCT ?item"):
            bindings = []
            for prop in PROPERTIES:
                if f"wdt:{prop} ?id" in query:
                    bindings = listing(prop)
            suffix = re.search(r'STRENDS\(STR\(\?item\), "(\d+)"\)', query)
            if suffix:
                bindings = [b for b in bindings if b["item"]["value"].endswith(suffix.group(1))]
            return httpx.Response(200, json={"head": {"vars": ["item"]},
                                             "results": {"bindings": bindings}})  # fmt: skip
        wanted = set(re.findall(r"wd:(Q\d+)", query.split("VALUES ?item", 1)[1].split("}")[0]))
        bindings = [
            b
            for b in DETAILS["results"]["bindings"]
            if b["item"]["value"].rsplit("/", 1)[1] in wanted
        ]
        return httpx.Response(
            200, json={"head": DETAILS["head"], "results": {"bindings": bindings}}
        )


@pytest.fixture
def fake_wdqs(monkeypatch) -> FakeWdqs:
    server = FakeWdqs()
    monkeypatch.setattr(wikidata_orgs, "http_client",
                        lambda: httpx.Client(transport=httpx.MockTransport(server)))  # fmt: skip
    monkeypatch.setattr(wikidata_orgs, "MIN_INTERVAL", 0.0)
    monkeypatch.setattr(wikidata_orgs, "SLEEP", lambda s: None)
    return server


def test_wikidata_queries() -> None:
    sel = {s.name: s for s in wikidata_orgs.SELECTORS}
    q = wikidata_orgs.ids_query(sel["lei"], "07", ["Q20", "Q142"], 10)
    assert "wdt:P1278" in q and "VALUES ?ctry { wd:Q142 wd:Q20 }" in q
    assert 'STRENDS(STR(?item), "07")' in q and q.endswith("LIMIT 10")
    one = wikidata_orgs.ids_query(sel["industry_site"], country_items=["Q408"])
    assert one.endswith("?item wdt:P452 ?ind . ?item wdt:P856 ?site . ?item wdt:P17 wd:Q408 . }")
    assert "P297" in wikidata_orgs.countries_query(["no", "AU"])
    with pytest.raises(ValueError):
        wikidata_orgs.ids_query(sel["lei"], "7'")
    with pytest.raises(ValueError):
        wikidata_orgs.ids_query(sel["lei"], country_items=["Q1 }"])
    with pytest.raises(ValueError):
        wikidata_orgs.countries_query(["N0'"])
    with pytest.raises(ValueError):
        wikidata_orgs.details_query(["Q1", "Q2 }"])
    d = wikidata_orgs.details_query(["Q1", "Q42"])
    for prop in ("P856", "P1278", "P1616", "P2622", "P5531", "P2333", "P3548", "P3549", "P13850",
                 "P571", "P576", "P1128", "P452", "P159", "P17"):  # fmt: skip
        assert f"{prop} " in d or f"{prop}/" in d


def test_wikidata_bridge_end_to_end(tmp_path: Path, fake_wdqs: FakeWdqs) -> None:
    stats = wikidata_orgs.build(opts(tmp_path, contact="ops@example.org"))
    assert all("contact: ops@example.org" in a for a in fake_wdqs.agents)
    names = sorted(p.name for p in stats.files)
    assert names == [f"wikidata_orgs-{cc}.parquet" for cc in ("AU", "CA", "FR", "GB", "NO", "US")]
    got = {}
    for path in stats.files:
        for r in rows(path):
            assert list(r) == list(PACK_COLUMNS)
            assert path.name == f"wikidata_orgs-{r['country']}.parquet"
            got[r["wikidata_id"]] = r
    assert len(got) == 22 and stats.rows == 22 and "1 dissolved" in stats.notes
    assert "Q7296793" not in got  # Ravn Studio, dissolved in 2025
    reuters = got["Q130879"]  # GB company with a Norwegian branch number: its own register wins
    assert (reuters["registry"], reuters["company_number"], reuters["country"]) == (
        "GB-CH",
        "03477402",
        "GB",
    )
    assert reuters["id"] == make_id("co", "reg", "GB-CH:03477402")
    assert reuters["domain"] == "reuters.com" and reuters["website"] == "https://www.reuters.com/"
    cme = got["Q1023876"]  # P17 US and GB, a CIK: US
    assert (cme["country"], cme["registry"], cme["cik"]) == ("US", "US-SEC", "0001156375")
    assert cme["company_number"] == "0001156375" and cme["lei"] == "LCZ7XYGSLJUHFXXNXD88"
    air_liquide = got["Q407448"]  # P17 CA and FR, a SIREN: France
    assert (air_liquide["country"], air_liquide["registry"]) == ("FR", "FR-SIREN")
    assert air_liquide["siren"] == air_liquide["company_number"] == "552096281"
    air_canada = got["Q185339"]  # has an Australian ABN too; Canada's own register wins
    assert (air_canada["registry"], air_canada["company_number"]) == ("CA-CORP", "4396626")
    cn = got["Q624798"]  # P17 CA and US: the Canadian number decides
    assert (cn["country"], cn["company_number"], cn["cik"]) == ("CA", "0105333", "0000016868")
    reed = got["Q488669"]  # no P17: the Companies House number gives the country
    assert (reed["country"], reed["company_number"]) == ("GB", "00151537")
    assert got["Q908144"]["company_number"] == "28008984049"  # Wesfarmers ABN
    vestre = got["Q100466633"]
    assert (vestre["registry"], vestre["company_number"]) == ("NO-BRREG", "948140349")
    assert vestre["domain"] == "vestre.com" and vestre["founded_year"] == 1947
    assert (vestre["employees"], vestre["size_range"]) == (22, "11-50")
    assert vestre["industry"] == "C" and vestre["industry_label"] == "furniture manufacturer"
    assert vestre["city"] == "Oslo" and vestre["name"] == "Vestre AS"
    assert got["Q190464"]["employees"] == 219697  # HSBC: the latest of 8 dated counts (2021)
    assert got["Q460487"]["employees"] == 11398  # Dassault: 2017 beats 2016
    assert got["Q188920"]["domain"] == "delta.com"
    assert got["Q216272"]["domain"] is None and got["Q216272"]["website"] is None
    assert json.loads(vestre["sources_json"])[0]["licence"] == "CC0-1.0"


def test_wikidata_resumes_from_the_work_dir(tmp_path: Path, fake_wdqs: FakeWdqs) -> None:
    first = wikidata_orgs.build(opts(tmp_path))
    n = len(fake_wdqs.queries)
    again = wikidata_orgs.build(opts(tmp_path))
    assert len(fake_wdqs.queries) == n  # every chunk came from the work dir
    assert again.rows == first.rows


def test_wikidata_country_run_and_test_mode(tmp_path: Path, fake_wdqs: FakeWdqs) -> None:
    stats = wikidata_orgs.build(opts(tmp_path, countries=frozenset({"NO"}), limit=3))
    assert [p.name for p in stats.files] == ["wikidata_orgs-NO.parquet"]
    assert stats.rows == 3
    ids_queries = [q for q in fake_wdqs.queries if q.startswith("SELECT DISTINCT")]
    assert not any("P1278" in q or "P856 ?site . FILTER" in q for q in ids_queries)
    assert not any("wdt:P2622" in q for q in ids_queries)  # a GB-only selector
    assert all("LIMIT" not in q for q in ids_queries)  # national selectors are listed in full
    full = FakeWdqs()
    client = httpx.Client(transport=httpx.MockTransport(full))
    fetch = wikidata_orgs.Fetch(
        wikidata_orgs.Sparql(client, min_interval=0, sleep=lambda s: None),
        tmp_path / "full",
        frozenset({"NO"}),
    )
    fetch.item_ids()
    listed = [q for q in full.queries if q.startswith("SELECT DISTINCT")]
    worldwide = [q for q in listed if "P1278" in q or "P856 ?site" in q and "P1616" not in q]
    assert len(worldwide) == 3  # LEI, company classes, industry: one query each for Norway
    assert all("wdt:P17 wd:Q20 ." in q and "STRENDS" not in q for q in worldwide)


def test_wikidata_splits_chunks_that_time_out(tmp_path: Path, monkeypatch) -> None:
    def fail(query: str) -> bool:
        unsplit_no = "wdt:P2333 ?id" in query and "STRENDS" not in query
        big_batch = "VALUES ?item" in query and len(re.findall(r"wd:Q\d+", query)) > 2
        return unsplit_no or big_batch

    server = FakeWdqs(fail=fail)
    monkeypatch.setattr(wikidata_orgs, "http_client",
                        lambda: httpx.Client(transport=httpx.MockTransport(server)))  # fmt: skip
    monkeypatch.setattr(wikidata_orgs, "MIN_INTERVAL", 0.0)
    monkeypatch.setattr(wikidata_orgs, "SLEEP", lambda s: None)
    monkeypatch.setattr(wikidata_orgs, "MAX_CONSECUTIVE_FAILURES", 1000)
    stats = wikidata_orgs.build(opts(tmp_path, countries=frozenset({"NO"})))
    no_rows = rows(stats.files[0])
    assert {r["wikidata_id"] for r in no_rows} >= {"Q108025", "Q697074", "Q1208134"}
    assert any('STRENDS(STR(?item), "5")' in q and "P2333" in q for q in server.queries)
    unsplit = [q for q in server.queries if "wdt:P2333 ?id" in q and "STRENDS" not in q]
    assert len(unsplit) == 2  # a timed-out query is retried once, then split
    assert "chunks failed" not in stats.notes


def test_sparql_client_backs_off_and_gives_up() -> None:
    ok = {"results": {"bindings": [{"x": {"value": "1"}}]}}
    answers = [httpx.Response(429, headers={"Retry-After": "7"}), httpx.Response(200, json=ok)]
    slept: list[float] = []
    client = httpx.Client(transport=httpx.MockTransport(lambda r: answers.pop(0)))
    sparql = wikidata_orgs.Sparql(client, min_interval=0, sleep=slept.append)
    assert sparql.select("SELECT 1") == [{"x": {"value": "1"}}] and 7.0 in slept
    down = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    sparql = wikidata_orgs.Sparql(down, min_interval=0, sleep=lambda s: None)
    for _ in range(wikidata_orgs.MAX_CONSECUTIVE_FAILURES - 1):
        with pytest.raises(wikidata_orgs.SparqlError):
            sparql.select("SELECT 1")
    with pytest.raises(RuntimeError, match="in a row"):
        sparql.select("SELECT 1")
    bad = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(400, text="no")))
    with pytest.raises(wikidata_orgs.SparqlError, match="400"):
        wikidata_orgs.Sparql(bad, min_interval=0, sleep=lambda s: None).select("SELECT")


def test_fixtures_copy_the_real_shapes() -> None:
    enheter = json.loads((FIX / "brreg_enheter.json").read_text(encoding="utf-8"))
    assert {"organisasjonsnummer", "navn", "organisasjonsform", "_links"} <= set(enheter[0])
    header = bulk.csv_header(FIX / "corporations-active-cbca-en.csv")
    assert len(header) == 18 and header[0] == "Corporation number" and "Status Detail" in header
    assert b"<Transfer>" in (FIX / "abr" / "20261111_Public01.xml").read_bytes()
    assert {"item", "label", "website", "countries"} <= set(DETAILS["results"]["bindings"][0])
