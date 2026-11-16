"""Entity resolution across bulk register shards, set-based in DuckDB.

Every shard row (see connectors/bulk) is a node. Rows are linked by the keys they carry, then
grouped into companies. Nothing here loops over rows in Python: each step is one SQL statement
over the whole table, so ~30M rows fit a 4-core / 16 GB runner (memory is bounded by DuckDB's
`memory_limit` and spills to the work directory).

Matching rules, in order:

1. **Strong keys.** Each row yields its normalised keys:
   `registry + company_number` -> `r|<REGISTRY>|<number>` (a SIREN, from the `siren` column or a
   `FR-SIREN` pair, is `r|FR-SIREN|<9 digits>`), `cik` (or a `US-SEC` pair) -> `c|<10 digits>`,
   `lei` -> `l|<LEI>`, `wikidata_id` -> `w|Q<n>`. Rows sharing a key belong together, and the
   links are transitive (A~B by LEI and B~C by SIREN put A, B and C together): connected
   components by min-label propagation with pointer jumping, a handful of rounds.
2. **Register guard.** A component may hold one number per register, and register numbers of
   one country only (`US-SEC` is exempt: foreign companies file with the SEC). A component that
   breaks this (a bad bridge row, e.g. a Wikidata item whose LEI points elsewhere) is re-solved
   conservatively: every row with a register number anchors to that number; the other rows join
   an anchor only through a key (LEI, then CIK, then Wikidata id) that points to exactly one
   anchor; the rest stay on their own. Two numbers of one register are never merged.
3. **Fallback** for clusters without a register number (GLEIF rows without a known registration
   authority, Wikidata items without registry ids): same country + normalised name + postcode,
   or + city when the unanchored side has no postcode. A group merges only when it holds at most
   one cluster with a register number and no two different LEIs, CIKs, Wikidata ids or domains.
   Rows of two register numbers are never merged by name.

`load_rows` creates the table `rk` (one row per shard row, normalised keys), `link` creates
`links(rid, cid, how)` for rows in clusters of two or more rows and `ccountry(cid, cc)` (the
cluster's country). Row ids (`rid`) are `<shard index> * 2^40 + <row number in the shard>`, so the
merge can read the shards again and find each row's cluster.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from pepite.search import PACK_COLUMNS, PACK_FLOAT_COLUMNS, PACK_INT_COLUMNS

log = logging.getLogger(__name__)

# The register each country's companies are identified by (Company.registry values). The stable
# id prefers this register's number when a company carries several.
NATIONAL_REGISTERS: dict[str, str] = {
    "FR": "FR-SIREN",
    "GB": "GB-CH",
    "NO": "NO-BRREG",
    "AU": "AU-ABN",
    "CA": "CA-CORP",
    "US": "US-SEC",
}
# Registers whose numbers may belong to companies of any country (exempt from the one-country rule).
INTERNATIONAL_REGISTERS: frozenset[str] = frozenset({"US-SEC"})
# French overseas departments and collectivities: GLEIF writes their own ISO codes, SIRENE files
# them under FR. Their companies go in the France pack (city and region are kept).
COUNTRY_ALIASES: dict[str, str] = {
    cc: "FR" for cc in ("GP", "MQ", "GF", "RE", "YT", "PM", "BL", "MF", "NC", "PF", "WF")
}
# Registers whose numbers have a fixed width (normalised by padding); the numbers of every other
# register compare without leading zeros ("0123456" = "123456", e.g. CA-CORP).
PADDED_REGISTERS: tuple[str, ...] = ("FR-SIREN", "US-SEC", "GB-CH", "NO-BRREG", "AU-ABN")
# Spellings other sources may use for the same register.
REGISTRY_ALIASES: dict[str, str] = {
    "SIREN": "FR-SIREN",
    "FR-SIRENE": "FR-SIREN",
    "INSEE": "FR-SIREN",
    "SEC": "US-SEC",
    "CIK": "US-SEC",
    "US-CIK": "US-SEC",
    "US-EDGAR": "US-SEC",
    "UK-CH": "GB-CH",
    "GB-COH": "GB-CH",
    "BRREG": "NO-BRREG",
    "NO-ORGNR": "NO-BRREG",
    "ABN": "AU-ABN",
}

# Source tiers: how far each source is trusted for which field (see merge.FIELD_PRIORITY),
# by source name prefix (gleif_golden, edgar_bulk, wikidata...). Every other source is a
# national register (sirene_stock, companies_house, Brønnøysund, ABR, Corporations Canada, and
# any register added later).
SOURCE_TIERS: dict[str, str] = {
    "gleif": "lei",
    "wikidata": "wiki",
    "edgar": "filings",
    "sitescan": "web",
}
TIERS: tuple[str, ...] = ("register", "filings", "lei", "wiki")
# Order used to pick a cluster's representative row (country, legal facts).
LEGAL_ORDER: tuple[str, ...] = ("register", "filings", "lei", "wiki")

# A key shared by more rows than this is a placeholder or junk value: it links nothing.
HUB_KEY_ROWS = 1000
MAX_ROUNDS = 64
RID_SPAN = 1 << 40  # rows per shard file in the rid space

# Words dropped from names before the fallback match (legal forms and fillers).
LEGAL_WORDS: tuple[str, ...] = tuple(
    """the and et und og sa sas sasu sarl eurl sci snc scs sca selarl scp scop gie eirl ltd
    limited plc llp lp llc inc incorporated corp corporation co company cie gmbh mbh ag kg ohg
    ug se nv bv spa srl sl sau as asa ab oy oyj aps pty pte bhd""".split()
)

KEY_TYPES = {"r": "registry", "c": "cik", "l": "lei", "w": "wikidata"}


def source_tier(source: str) -> str:
    """Tier of a source name: 'register' (national registers), 'filings', 'lei' or 'wiki'."""
    name = source.lower()
    return next((t for p, t in SOURCE_TIERS.items() if name.startswith(p)), "register")


def tier_rank_sql(order: Sequence[str], col: str = "tier") -> str:
    """CASE expression ranking `col` by `order` (0 = most trusted)."""
    whens = " ".join(f"WHEN '{t}' THEN {i}" for i, t in enumerate(order))
    return f"(CASE {col} {whens} ELSE {len(order)} END)"


def lit(text: str) -> str:
    """SQL string literal."""
    return "'" + str(text).replace("'", "''") + "'"


def _sql_type(col: str) -> str:
    if col in PACK_INT_COLUMNS:
        return "INTEGER"
    return "DOUBLE" if col in PACK_FLOAT_COLUMNS else "VARCHAR"


# -- shards -------------------------------------------------------------------------------

_SHARD_RE = re.compile(r"^(?P<source>.+?)(?:-(?P<cc>[A-Z]{2}))?$")


@dataclass(frozen=True)
class Shard:
    path: Path
    source: str
    country: str | None = None

    @property
    def tier(self) -> str:
        return source_tier(self.source)


def find_shards(shards_dir: Path) -> list[Shard]:
    """`<source>.parquet` and `<source>-<CC>.parquet` files of a shards directory, sorted."""
    out = []
    for p in sorted(Path(shards_dir).glob("*.parquet")):
        if p.name.startswith((".", "_")) or not p.is_file():
            continue
        m = _SHARD_RE.match(p.stem)
        assert m is not None  # the pattern matches any non-empty stem
        out.append(Shard(p.resolve(), m["source"].lower(), m["cc"]))
    return out


# -- SQL macros ----------------------------------------------------------------------------


def install_macros(con) -> None:
    """Normalisation macros shared by linking, merging and quality (temporary, per connection)."""
    legal = "|".join(LEGAL_WORDS)
    national = " ".join(f"WHEN '{cc}' THEN '{reg}'" for cc, reg in NATIONAL_REGISTERS.items())
    aliases = " ".join(f"WHEN '{a}' THEN '{r}'" for a, r in REGISTRY_ALIASES.items())
    squeezed = "nullif(regexp_replace(upper(CAST(x AS VARCHAR)), '\\s+', '', 'g'), '')"
    overseas = " ".join(f"WHEN '{a}' THEN '{c}'" for a, c in COUNTRY_ALIASES.items())
    padded = ", ".join(lit(r) for r in PADDED_REGISTERS)
    macros = [
        "lk_txt(x) AS nullif(trim(CAST(x AS VARCHAR)), '')",
        f"lk_cc(x) AS CASE upper(trim(CAST(x AS VARCHAR))) {overseas} ELSE CASE WHEN "
        "regexp_full_match(upper(trim(CAST(x AS VARCHAR))), '[A-Z]{2}') "
        "THEN upper(trim(CAST(x AS VARCHAR))) END END",
        "lk_alnum(x) AS "
        "nullif(regexp_replace(upper(CAST(x AS VARCHAR)), '[^A-Z0-9]', '', 'g'), '')",
        "lk_digits(x) AS nullif(regexp_replace(CAST(x AS VARCHAR), '[^0-9]', '', 'g'), '')",
        "lk_nonzero(x) AS CASE WHEN x IS NOT NULL AND NOT regexp_full_match(x, '0+') THEN x END",
        f"lk_national(cc) AS CASE cc {national} END",
        f"lk_registry(x) AS CASE {squeezed} {aliases} ELSE {squeezed} END",
        # SIREN: 9 digits (a SIRET's first 9; leading zeros restored when an integer lost them).
        "lk_siren(x) AS lk_nonzero(CASE WHEN length(lk_digits(x)) BETWEEN 8 AND 9 "
        "THEN lpad(lk_digits(x), 9, '0') WHEN length(lk_digits(x)) = 14 "
        "THEN left(lk_digits(x), 9) END)",
        "lk_cik(x) AS lk_nonzero(CASE WHEN length(lk_digits(x)) <= 10 "
        "THEN lpad(lk_digits(x), 10, '0') END)",
        # Companies House: 8 characters, digits zero-padded (SC12345 -> SC012345).
        "lk_ch(x) AS CASE WHEN regexp_full_match(lk_alnum(x), '[0-9]{1,8}') "
        "THEN lpad(lk_alnum(x), 8, '0') WHEN regexp_full_match(lk_alnum(x), '[A-Z]{2}[0-9]{1,6}') "
        "THEN left(lk_alnum(x), 2) || lpad(lk_alnum(x)[3:], 6, '0') ELSE lk_alnum(x) END",
        "lk_number(reg, num) AS lk_nonzero(CASE WHEN reg IS NULL THEN NULL "
        "WHEN reg = 'FR-SIREN' THEN lk_siren(num) WHEN reg = 'US-SEC' THEN lk_cik(num) "
        "WHEN reg = 'GB-CH' THEN lk_ch(num) "
        "WHEN reg IN ('NO-BRREG', 'AU-ABN') THEN lk_digits(num) "
        "WHEN regexp_full_match(lk_alnum(num), '[0-9]+') THEN ltrim(lk_alnum(num), '0') "
        "ELSE lk_alnum(num) END)",
        # The number as stored: padded registers as compared, others as written (alphanumeric).
        f"lk_number_disp(reg, num) AS CASE WHEN reg IN ({padded}) THEN lk_number(reg, num) "
        "WHEN lk_number(reg, num) IS NOT NULL THEN lk_alnum(num) END",
        "lk_lei(x) AS CASE WHEN regexp_full_match(lk_alnum(x), '[A-Z0-9]{20}') "
        "THEN lk_alnum(x) END",
        "lk_wd(x) AS 'Q' || nullif(regexp_extract(upper(CAST(x AS VARCHAR)), 'Q([0-9]+)', 1), '')",
        "lk_domain(x) AS "
        "nullif(regexp_replace(lower(trim(CAST(x AS VARCHAR))), '^www\\.', ''), '')",
        # Fallback keys: lowercase ASCII words without legal forms / postcode without spaces.
        "lk_name_key(x) AS trim(regexp_replace(regexp_replace(regexp_replace("
        "lower(strip_accents(replace(replace(CAST(x AS VARCHAR), '.', ''), '&', ' and '))), "
        f"'[^a-z0-9]+', ' ', 'g'), '\\b({legal})\\b', ' ', 'g'), '\\s+', ' ', 'g'))",
        "lk_pc_key(x, cc) AS CASE WHEN cc = 'US' THEN left(lk_digits(x), 5) ELSE lk_alnum(x) END",
        "lk_city_key(x) AS nullif(trim(regexp_replace(regexp_replace(regexp_replace("
        "lower(strip_accents(CAST(x AS VARCHAR))), '[^a-z]+', ' ', 'g'), "
        "'\\b(cedex|arrondissement|er|e)\\b', ' ', 'g'), '\\s+', ' ', 'g')), '')",
        "lk_status(x) AS CASE WHEN lk_txt(x) IS NULL THEN NULL "
        "WHEN lower(trim(x)) IN ('inactive', 'dissolved', 'closed', 'ceased', 'struck off', "
        "'struck-off', 'removed', 'liquidated', 'deregistered', 'cancelled', 'retired', "
        "'merged', 'converted') THEN 'inactive' "
        "WHEN lower(trim(x)) IN ('active', 'live', 'registered', 'open') THEN 'active' "
        "ELSE lower(trim(x)) END",
        # pepite.models.make_id("co", kind, value), for ASCII kind and value.
        "lk_make_id(kind, val) AS 'co_' || left(sha1(lower(trim(kind)) || '|' || "
        "lower(trim(val))), 16)",
    ]
    for m in macros:
        con.execute(f"CREATE OR REPLACE TEMP MACRO {m}")


def norm_select_sql(tier_col: str = "tier") -> str:
    """Normalised key columns of a shard row (FROM a relation with the raw PACK_COLUMNS,
    `country`-like inputs and a tier column). Used by load_rows and by merge."""
    return f"""
      country_n, reg_registry, reg_number, siren_n, cik_n, lei_n, wd_n, domain_n,
      CASE WHEN pn IS NOT NULL AND pr NOT IN ('FR-SIREN', 'US-SEC')
           THEN 'r|' || pr || '|' || pn END AS k_reg,
      CASE WHEN siren_n IS NOT NULL THEN 'r|FR-SIREN|' || siren_n END AS k_siren,
      CASE WHEN cik_n IS NOT NULL THEN 'c|' || cik_n END AS k_cik,
      CASE WHEN lei_n IS NOT NULL THEN 'l|' || lei_n END AS k_lei,
      CASE WHEN wd_n IS NOT NULL THEN 'w|' || wd_n END AS k_wd,
      {tier_rank_sql(LEGAL_ORDER, tier_col)} AS rk_legal,
      coalesce(reg_registry || ':' || reg_number, lei_n, cik_n, wd_n, domain_n,
               lower(lk_txt(name)), '') || '|' || src AS natkey
    """


def norm_from_sql(raw: str, tier_col: str = "tier") -> str:
    """Nested FROM computing the inputs of norm_select_sql over relation `raw`."""
    return f"""(
      SELECT *,
        CASE WHEN pn IS NOT NULL AND pr = lk_national(country_n) THEN pr
             WHEN siren_n IS NOT NULL AND country_n = 'FR' THEN 'FR-SIREN'
             WHEN pn IS NOT NULL THEN pr
             WHEN siren_n IS NOT NULL THEN 'FR-SIREN'
             WHEN cik_n IS NOT NULL AND {tier_col} = 'filings' THEN 'US-SEC' END AS reg_registry,
        CASE WHEN pn IS NOT NULL AND pr = lk_national(country_n) THEN pn
             WHEN siren_n IS NOT NULL AND country_n = 'FR' THEN siren_n
             WHEN pn IS NOT NULL THEN pn
             WHEN siren_n IS NOT NULL THEN siren_n
             WHEN cik_n IS NOT NULL AND {tier_col} = 'filings' THEN cik_n END AS reg_number,
        CASE WHEN pn IS NOT NULL AND pr = lk_national(country_n) THEN pn_disp
             WHEN siren_n IS NOT NULL AND country_n = 'FR' THEN siren_n
             WHEN pn IS NOT NULL THEN pn_disp
             WHEN siren_n IS NOT NULL THEN siren_n
             WHEN cik_n IS NOT NULL AND {tier_col} = 'filings' THEN cik_n END AS reg_disp
      FROM (
        SELECT *,
          coalesce(CASE WHEN pr = 'FR-SIREN' THEN pn END, lk_siren(siren)) AS siren_n,
          coalesce(CASE WHEN pr = 'US-SEC' THEN pn END, lk_cik(cik)) AS cik_n,
          lk_lei(lei) AS lei_n, lk_wd(wikidata_id) AS wd_n, lk_domain(domain) AS domain_n
        FROM (
          SELECT *, lk_number(pr, company_number) AS pn,
            lk_number_disp(pr, company_number) AS pn_disp
          FROM (SELECT *, lk_cc(country) AS country_n, lk_registry(registry) AS pr FROM {raw})
        )
      )
    )"""


def shards_union_sql(con, shards: Sequence[Shard], columns: Sequence[str] = PACK_COLUMNS) -> str:
    """One relation over every shard: rid, src, tier and the given pack columns (NULL when a
    shard lacks one). A UNION ALL of one scan per file, so DuckDB knows every row count (a join
    on the file name would blur its estimates and its choice of hash-join sides)."""
    parts = []
    for i, s in enumerate(shards):
        path = lit(str(s.path))
        described = con.execute(f"DESCRIBE SELECT * FROM read_parquet({path})").fetchall()
        have = {r[0] for r in described}
        missing = [c for c in columns if c not in have]
        if missing:
            log.warning("%s lacks columns %s (read as NULL)", s.path.name, missing)
        cols = ", ".join(
            f"CAST({c} AS {_sql_type(c)}) AS {c}"
            if c in have
            else f"CAST(NULL AS {_sql_type(c)}) AS {c}"
            for c in columns
        )
        parts.append(
            f"SELECT {i}::BIGINT * {RID_SPAN} + file_row_number AS rid, {lit(s.source)} AS src, "
            f"{lit(s.tier)} AS tier, {cols} FROM read_parquet({path}, file_row_number = true)"
        )
    return "(" + "\n      UNION ALL ".join(parts) + ")"


# -- loading -------------------------------------------------------------------------------


def load_rows(con, shards: Sequence[Shard]) -> dict[str, int]:
    """Create table `rk`: one row per shard row with normalised keys and fallback keys.
    Returns input rows per source."""
    t0 = time.monotonic()
    raw = shards_union_sql(
        con, shards, ("country", "registry", "company_number", "siren", "cik", "lei",
                      "wikidata_id", "domain", "name", "legal_name", "postal_code", "city"),
    )  # fmt: skip
    con.execute(f"""
      CREATE OR REPLACE TABLE rk AS
      SELECT rid, src, tier, {norm_select_sql()},
        lk_name_key(coalesce(lk_txt(name), legal_name)) AS name_key,
        lk_pc_key(postal_code, country_n) AS pc_key,
        lk_city_key(city) AS city_key
      FROM {norm_from_sql(raw)}
    """)
    counts = {
        str(s): int(n)
        for s, n in con.execute("SELECT src, count(*) FROM rk GROUP BY src ORDER BY src").fetchall()
    }
    log.info("loaded %d rows from %d shards (%.1f s): %s", sum(counts.values()), len(shards),
             time.monotonic() - t0, counts)  # fmt: skip
    return counts


# -- linking -------------------------------------------------------------------------------


@dataclass
class LinkStats:
    rows: int = 0
    linking_keys: int = 0  # distinct keys shared by two or more rows
    hub_keys: int = 0  # keys shared by more than HUB_KEY_ROWS rows (ignored)
    linked_rows: int = 0  # rows that share a key with another row
    rounds: int = 0
    components: int = 0
    conflicted_components: int = 0
    conflicted_rows: int = 0
    detached_rows: int = 0  # rows of conflicted components left on their own
    fallback_postcode: int = 0  # clusters merged by name + postcode
    fallback_city: int = 0  # clusters merged by name + city
    clusters: int = 0  # final clusters with two or more rows
    clustered_rows: int = 0
    seconds: float = 0.0
    timings: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


def _count(con, sql: str) -> int:
    return int(con.execute(sql).fetchone()[0] or 0)


def _key_union(cols: Sequence[str] = ("k_reg", "k_siren", "k_cik", "k_lei", "k_wd")) -> str:
    return " UNION ALL ".join(f"SELECT rid, {c} AS k FROM rk WHERE {c} IS NOT NULL" for c in cols)


def link(con, hub_rows: int = HUB_KEY_ROWS) -> LinkStats:
    """Resolve entities over `rk`; creates `links(rid, cid, how)` and `ccountry(cid, cc)`."""
    st = LinkStats(rows=_count(con, "SELECT count(*) FROM rk"))
    t_all = time.monotonic()

    def step(name: str, t0: float) -> None:
        st.timings[name] = round(time.monotonic() - t0, 2)
        log.info("link: %s (%.1f s)", name, st.timings[name])

    # 1. Keys shared by at least two rows: hash prefilter (integers), then exact strings.
    t0 = time.monotonic()
    con.execute(f"""
      CREATE OR REPLACE TABLE hc AS
      SELECT h FROM (SELECT hash(k) AS h FROM ({_key_union()})) GROUP BY h HAVING count(*) > 1
    """)
    con.execute(f"""
      CREATE OR REPLACE TABLE ecand AS
      SELECT rid, k FROM ({_key_union()}) WHERE hash(k) IN (SELECT h FROM hc)
    """)
    con.execute("""
      CREATE OR REPLACE TABLE kcount AS
      SELECT k, count(DISTINCT rid) AS n FROM ecand GROUP BY k HAVING count(DISTINCT rid) > 1
    """)
    st.hub_keys = _count(con, f"SELECT count(*) FROM kcount WHERE n > {int(hub_rows)}")
    if st.hub_keys:
        log.warning("link: %d keys shared by more than %d rows ignored: %s", st.hub_keys,
                    hub_rows, con.execute(f"SELECT list(k) FROM (SELECT k FROM kcount WHERE "
                                          f"n > {int(hub_rows)} ORDER BY n DESC LIMIT 5)"
                                          ).fetchone()[0])  # fmt: skip
    con.execute(f"""
      CREATE OR REPLACE TABLE kid AS
      SELECT k, row_number() OVER (ORDER BY k) AS kid FROM kcount WHERE n <= {int(hub_rows)}
    """)
    con.execute("""
      CREATE OR REPLACE TABLE e AS
      SELECT DISTINCT c.rid, c.k, kid.kid FROM ecand c JOIN kid USING (k)
    """)
    con.execute("DROP TABLE hc; DROP TABLE ecand")
    st.linking_keys = _count(con, "SELECT count(*) FROM kid")
    st.linked_rows = _count(con, "SELECT count(DISTINCT rid) FROM e")
    step("keys", t0)

    # 2. Connected components: min-label propagation over the row-key graph + pointer jumping.
    t0 = time.monotonic()
    con.execute("CREATE OR REPLACE TABLE lab AS SELECT DISTINCT rid, rid AS l FROM e")
    for rnd in range(1, MAX_ROUNDS + 1):
        con.execute("""
          CREATE OR REPLACE TABLE kl AS
          SELECT e.kid, min(lab.l) AS l FROM e JOIN lab USING (rid) GROUP BY e.kid
        """)
        con.execute("""
          CREATE OR REPLACE TABLE lab1 AS
          SELECT e.rid, min(kl.l) AS l FROM e JOIN kl USING (kid) GROUP BY e.rid
        """)
        con.execute("""
          CREATE OR REPLACE TABLE lab2 AS
          SELECT a.rid, least(a.l, b.l) AS l FROM lab1 a LEFT JOIN lab1 b ON b.rid = a.l
        """)
        changed = _count(con, "SELECT count(*) FROM lab2 n JOIN lab o USING (rid) WHERE n.l <> o.l")
        con.execute("DROP TABLE lab; ALTER TABLE lab2 RENAME TO lab; DROP TABLE lab1")
        st.rounds = rnd
        if not changed:
            break
    else:
        log.warning("link: labels still changing after %d rounds", MAX_ROUNDS)
    con.execute("DROP TABLE IF EXISTS kl")
    st.components = _count(con, "SELECT count(DISTINCT l) FROM lab")
    step(f"components ({st.rounds} rounds)", t0)

    # 3. Register guard: components with two numbers of one register, or registers of two
    # countries, are re-solved around their register numbers.
    t0 = time.monotonic()
    intl = ", ".join(lit(r) for r in sorted(INTERNATIONAL_REGISTERS))
    con.execute(f"""
      CREATE OR REPLACE TABLE creg AS
      WITH g AS (SELECT lab.l, rk.* FROM lab JOIN rk USING (rid)),
      regs AS (
        SELECT l, split_part(k_reg, '|', 2) AS registry, split_part(k_reg, '|', 3) AS num
          FROM g WHERE k_reg IS NOT NULL
        UNION ALL SELECT l, 'FR-SIREN', siren_n FROM g WHERE siren_n IS NOT NULL
        UNION ALL SELECT l, 'US-SEC', cik_n FROM g WHERE cik_n IS NOT NULL
      ),
      per_reg AS (SELECT l, registry, count(DISTINCT num) AS nums FROM regs GROUP BY l, registry)
      SELECT l FROM per_reg GROUP BY l
      HAVING max(nums) > 1
          OR count(DISTINCT CASE WHEN registry NOT IN ({intl})
                                  AND regexp_full_match(registry, '[A-Z]{{2}}-.+')
                                 THEN left(registry, 2) END) > 1
    """)
    st.conflicted_components = _count(con, "SELECT count(*) FROM creg")
    con.execute("""
      CREATE OR REPLACE TABLE crows AS
      SELECT lab.rid, lab.l, rk.reg_registry, rk.reg_number
      FROM lab JOIN creg USING (l) JOIN rk USING (rid)
    """)
    st.conflicted_rows = _count(con, "SELECT count(*) FROM crows")
    con.execute("""
      CREATE OR REPLACE TABLE asg AS
      SELECT rid, min(rid) OVER (PARTITION BY l, reg_registry, reg_number) AS a
      FROM crows WHERE reg_registry IS NOT NULL
    """)
    for _ in range(3):  # rows without a register number join through LEI, CIK, Wikidata id
        added = con.execute("""
          INSERT INTO asg
          WITH km AS (
            SELECT e.k, min(asg.a) AS amin, max(asg.a) AS amax
            FROM e JOIN asg USING (rid) JOIN crows USING (rid) GROUP BY e.k
          ),
          cand AS (
            SELECT c.rid, left(e.k, 1) AS t, min(km.amin) AS amin, max(km.amax) AS amax
            FROM crows c JOIN e USING (rid) JOIN km USING (k)
            WHERE c.rid NOT IN (SELECT rid FROM asg) AND left(e.k, 1) IN ('l', 'c', 'w')
            GROUP BY c.rid, t
          ),
          best AS (
            SELECT *, row_number() OVER (PARTITION BY rid ORDER BY
              CASE t WHEN 'l' THEN 0 WHEN 'c' THEN 1 ELSE 2 END) AS pick
            FROM cand
          )
          SELECT rid, amin FROM best WHERE pick = 1 AND amin = amax
        """).fetchone()
        if not added or not added[0]:
            break
    st.detached_rows = _count(con, "SELECT count(*) FROM crows ANTI JOIN asg USING (rid)")
    con.execute("""
      CREATE OR REPLACE TABLE sl AS
      SELECT lab.rid, CASE WHEN c.rid IS NULL THEN lab.l ELSE coalesce(asg.a, lab.rid) END AS cid
      FROM lab LEFT JOIN crows c USING (rid) LEFT JOIN asg USING (rid)
    """)
    step("register guard", t0)

    # 4. Fallback on name + postcode / city for clusters without a register number.
    t0 = time.monotonic()
    _fallback(con, st)
    step("fallback", t0)

    # 5. Final clusters (two or more rows), how each row joined, and the cluster's country.
    t0 = time.monotonic()
    con.execute("""
      CREATE OR REPLACE TABLE fin AS
      WITH allr AS (  -- rows of strong clusters, plus single rows the fallback merged
        SELECT rid, cid AS scid FROM sl
        UNION ALL
        SELECT x, x FROM (SELECT cid AS x FROM fb UNION SELECT target FROM fb)
        WHERE x NOT IN (SELECT rid FROM sl)
      )
      SELECT a.rid, a.scid, coalesce(fb.target, a.scid) AS cid, fb.how AS fhow
      FROM allr a LEFT JOIN fb ON fb.cid = a.scid
    """)
    con.execute("""
      CREATE OR REPLACE TABLE links AS
      WITH multi AS (
        SELECT * FROM fin WHERE cid IN (SELECT cid FROM fin GROUP BY cid HAVING count(*) > 1)
      ),
      strong AS (  -- strongest key a row shares with another row of its strong cluster
        SELECT e.rid, min(CASE WHEN e.k LIKE 'r|FR-SIREN|%' THEN 1 WHEN e.k LIKE 'r|%' THEN 0
                               WHEN e.k LIKE 'c|%' THEN 2 WHEN e.k LIKE 'l|%' THEN 3 ELSE 4 END
                          ) AS t
        FROM e JOIN multi m USING (rid)
        JOIN (SELECT e.k, m.scid FROM e JOIN multi m USING (rid)
              GROUP BY e.k, m.scid HAVING count(*) > 1) s ON s.k = e.k AND s.scid = m.scid
        GROUP BY e.rid
      ),
      ranked AS (
        SELECT m.*, s.t, row_number() OVER (PARTITION BY m.scid ORDER BY rk.rk_legal, rk.natkey)
               AS srep
        FROM multi m JOIN rk USING (rid) LEFT JOIN strong s USING (rid)
      )
      SELECT rid, cid,
        CASE WHEN fhow IS NOT NULL AND srep = 1 THEN fhow
             WHEN t = 0 THEN 'registry' WHEN t = 1 THEN 'siren' WHEN t = 2 THEN 'cik'
             WHEN t = 3 THEN 'lei' WHEN t = 4 THEN 'wikidata' ELSE fhow END AS how
      FROM ranked
    """)
    country_rank = tier_rank_sql(LEGAL_ORDER)
    con.execute(f"""
      CREATE OR REPLACE TABLE ccountry AS
      SELECT cid, arg_min(country_n, struct_pack(r := {country_rank}, k := natkey))
                  FILTER (WHERE country_n IS NOT NULL) AS cc
      FROM links JOIN rk USING (rid) GROUP BY cid
    """)
    st.clusters = _count(con, "SELECT count(*) FROM ccountry")
    st.clustered_rows = _count(con, "SELECT count(*) FROM links")
    for t in ("fin", "sl", "lab", "asg", "crows", "creg", "kcount", "kid"):
        con.execute(f"DROP TABLE IF EXISTS {t}")
    step("clusters", t0)
    st.seconds = round(time.monotonic() - t_all, 2)
    log.info("link: %s", st.as_dict())
    return st


def _fallback(con, st: LinkStats) -> None:
    """Create `fb(cid, target, how)`: clusters merged into another by name + postcode / city."""
    rep = "struct_pack(r := rk_legal, k := natkey)"
    attrs = f"""
      bool_or(reg_registry IS NOT NULL) AS anchored,
      arg_min(country_n, {rep}) FILTER (WHERE country_n IS NOT NULL) AS country,
      arg_min(name_key, {rep}) FILTER (WHERE name_key <> '') AS name_key,
      arg_min(struct_pack(pc := pc_key, city := city_key), {rep})
        FILTER (WHERE pc_key IS NOT NULL OR city_key IS NOT NULL) AS place,
      min(lei_n) AS lei_min, max(lei_n) AS lei_max, min(wd_n) AS wd_min, max(wd_n) AS wd_max,
      min(cik_n) AS cik_min, max(cik_n) AS cik_max,
      min(domain_n) AS dom_min, max(domain_n) AS dom_max
    """
    # Cluster attributes, kept only for clusters whose (country, name) is also the name of a
    # cluster without a register number: no other cluster can take part in a match.
    con.execute(f"""
      CREATE OR REPLACE TABLE cm AS
      SELECT cid, anchored, country, name_key, place.pc AS pc_key, place.city AS city_key,
             lei_min, lei_max, wd_min, wd_max, cik_min, cik_max, dom_min, dom_max
      FROM (SELECT sl.cid, {attrs} FROM sl JOIN rk USING (rid) GROUP BY sl.cid)
    """)
    con.execute("""
      CREATE OR REPLACE TABLE un AS
      SELECT DISTINCT country, name_key FROM (
        SELECT country, name_key FROM cm WHERE NOT anchored
        UNION ALL
        SELECT country_n, name_key FROM rk
        WHERE reg_registry IS NULL AND rid NOT IN (SELECT rid FROM sl)
      ) WHERE country IS NOT NULL AND name_key <> ''
    """)
    con.execute("""
      CREATE OR REPLACE TABLE ca AS
      SELECT * FROM cm SEMI JOIN un USING (country, name_key)
      UNION ALL
      SELECT rid, reg_registry IS NOT NULL, country_n, name_key, pc_key, city_key,
             lei_n, lei_n, wd_n, wd_n, cik_n, cik_n, domain_n, domain_n
      FROM (SELECT * FROM rk WHERE rid NOT IN (SELECT rid FROM sl)) r
      SEMI JOIN un ON un.country = r.country_n AND un.name_key = r.name_key
    """)
    con.execute("DROP TABLE cm; DROP TABLE un")
    compat = " AND ".join(
        f"(max({k}_max) IS NULL OR min({k}_min) = max({k}_max))"
        for k in ("lei", "wd", "cik", "dom")
    )

    def groups(key: str, extra: str) -> str:
        # Groups that hold at least one cluster without a register number and are unambiguous.
        return f"""
          WITH cand AS (
            SELECT * FROM ca
            WHERE country IS NOT NULL AND name_key IS NOT NULL AND {key} IS NOT NULL AND {extra}
          ),
          ua AS (SELECT DISTINCT country, name_key, {key} FROM cand WHERE NOT anchored),
          g AS (
            SELECT country, name_key, {key},
              coalesce(min(cid) FILTER (WHERE anchored), min(cid)) AS target
            FROM cand SEMI JOIN ua USING (country, name_key, {key})
            GROUP BY country, name_key, {key}
            HAVING count(*) >= 2 AND count(*) FILTER (WHERE anchored) <= 1 AND {compat}
          )
          SELECT c.cid, g.target FROM cand c JOIN g USING (country, name_key, {key})
          WHERE c.cid <> g.target
        """

    con.execute(f"""
      CREATE OR REPLACE TABLE fb AS
      SELECT cid, target, 'name_postcode' AS how FROM ({groups("pc_key", "true")})
    """)
    st.fallback_postcode = _count(con, "SELECT count(*) FROM fb")
    if st.fallback_postcode:  # fold the merged clusters' keys into their targets
        con.execute("""
          UPDATE ca SET lei_min = least(ca.lei_min, x.lei_min),
                        lei_max = greatest(ca.lei_max, x.lei_max),
                        wd_min = least(ca.wd_min, x.wd_min), wd_max = greatest(ca.wd_max, x.wd_max),
                        cik_min = least(ca.cik_min, x.cik_min),
                        cik_max = greatest(ca.cik_max, x.cik_max),
                        dom_min = least(ca.dom_min, x.dom_min),
                        dom_max = greatest(ca.dom_max, x.dom_max)
          FROM (SELECT fb.target, min(lei_min) AS lei_min, max(lei_max) AS lei_max,
                       min(wd_min) AS wd_min, max(wd_max) AS wd_max,
                       min(cik_min) AS cik_min, max(cik_max) AS cik_max,
                       min(dom_min) AS dom_min, max(dom_max) AS dom_max
                FROM fb JOIN ca USING (cid) GROUP BY fb.target) x
          WHERE ca.cid = x.target
        """)
        con.execute("DELETE FROM ca WHERE cid IN (SELECT cid FROM fb)")
    # City pass: unanchored clusters without a postcode against every anchored cluster.
    con.execute(f"""
      INSERT INTO fb
      SELECT cid, target, 'name_city' FROM ({groups("city_key", "(anchored OR pc_key IS NULL)")})
    """)
    st.fallback_city = _count(con, "SELECT count(*) FROM fb") - st.fallback_postcode
    con.execute("DROP TABLE ca")


__all__ = [
    "COUNTRY_ALIASES",
    "HUB_KEY_ROWS",
    "INTERNATIONAL_REGISTERS",
    "LEGAL_ORDER",
    "LinkStats",
    "NATIONAL_REGISTERS",
    "SOURCE_TIERS",
    "Shard",
    "TIERS",
    "find_shards",
    "install_macros",
    "link",
    "lit",
    "load_rows",
    "norm_from_sql",
    "norm_select_sql",
    "shards_union_sql",
    "source_tier",
    "tier_rank_sql",
]
