# Company search at pack scale

Packs grow from 13,625 companies (one file) to one Parquet file per country: up to ~15M rows for
one country and 30M+ in total. This page records how search stays fast on a laptop, with the
numbers behind each choice. Code: `src/pepite/packs/index.py` (storage),
`src/pepite/packs/duck.py` (queries), `tools/bench_search.py` (benchmark).

Targets (data roadmap, phase 1): p95 under 1.5 s for the results page, under 3 s for the filter
panel counts, memory a laptop survives, no DuckDB extension downloaded at runtime.

## Reproduce

```sh
.venv/bin/python tools/bench_search.py generate --rows 12000000 --out /tmp/bench-packs
.venv/bin/python tools/bench_search.py run --data /tmp/bench-packs --raw    # Parquet only (before)
.venv/bin/python tools/bench_search.py run --data /tmp/bench-packs --memory-limit 512MB
```

`generate` writes a synthetic release in the published layout (one `companies-<CC>-<date>.parquet`
per country, PACK_COLUMNS, `manifest.json` with sha256): 12.0M rows, 563 MB, FR 6.96M, GB 3.0M,
US 0.96M, DE 0.48M, NO 0.30M, BE/ES/IT ~0.1M each. Names are register-like (prefixes such as
SCI, SARL, Garage; surnames; business words; 40% upper case), cities Zipf-distributed over ~35k
names per country (Paris ~7% of FR), ISIC sections and size bands with register shares (55% no
size), 18% with a domain, 60% with a legal name, a registry number on every row.

`run` installs the files the way a download does (copy, then build the local search index, which
is timed), then times the query paths the UI uses, each with distinct filters so that no result
cache helps (every number below is a cold query for its filter):

- **results page**: what `/companies` asks the search for: the total (`search_companies(per_page=1)`)
  and a page of 25, for browse (pages 1, 2, 40), countries, country + industry + size, regions and
  cities, free text (company names and words), keywords (any / all / industry / exclusions);
- **facets**: the three lists of the filter panel (country 300, industry 300, size 20), each
  without its own filter group, as `pages.company_facets` asks;
- **full /companies request**: the two above together;
- **sorted by name**: `pages.scan_companies` (sort by name or size, tabs): up to 5,000 rows in
  pages of 500;
- **get_company(id)**, **lookup by domain** (`search_companies(domains=[d])`), **lookup by registry
  number**, **place suggestions**.

Machine: 4 vCPU, 15 GB RAM, SSD, Linux, Python 3.13, DuckDB 2.0.0.

## Before and after (12.0M rows, 8 country files)

Before: the previous `DuckDBCompanySearch` (a view over `read_parquet` of every file, `lower()`
and `LIKE` per request), DuckDB defaults (memory limit 80% of RAM, 4 threads).
After: the local index, memory limit 512 MB (the default on an 8 GB laptop), 4 threads.

| query path | before p50 | before p95 | after p50 | after p95 | after p95, 2 threads |
|---|---:|---:|---:|---:|---:|
| results page (count + 25 rows), all filters | 820 ms | 3,732 ms | 56 ms | 285 ms | 510 ms |
| … browse | 1,175 ms | 1,223 ms | 46 ms | 99 ms | 193 ms |
| … country | 410 ms | 812 ms | 10 ms | 50 ms | 60 ms |
| … country + industry + size | 615 ms | 1,063 ms | 13 ms | 39 ms | 51 ms |
| … region / city | 778 ms | 1,392 ms | 158 ms | 285 ms | 512 ms |
| … text | 3,501 ms | 3,732 ms | 234 ms | 302 ms | 552 ms |
| … keywords | 3,036 ms | 5,467 ms | 199 ms | 322 ms | 502 ms |
| facets (3 lists) | 311 ms | 2,221 ms | 4 ms | 127 ms | 245 ms |
| full /companies request | 1,503 ms | 5,961 ms | 165 ms | 325 ms | 575 ms |
| sorted by name (scan 5,000 rows) | 16,312 ms | 25,025 ms | 194 ms | 306 ms | 437 ms |
| get_company(id) | 47 ms | 60 ms | 9 ms | 13 ms | 13 ms |
| lookup by domain | 62 ms | 78 ms | 19 ms | 27 ms | 18 ms |
| lookup by registry number (before: column scan, no API) | 64 ms | 76 ms | 10 ms | 105 ms | 136 ms |
| place suggestions | 207 ms | 223 ms | 10 ms | 11 ms | 16 ms |

Runs differ by up to ~2x on the cold paths (what is in the OS page cache and in DuckDB's
buffer when a query starts); the table is one full run after a fresh build. The registry-number
p95 comes from synthetic GB numbers that repeat (those lookups return 10 companies, each a cold
row fetch).

Memory (resident size of the Python process):

| | before | after, 512 MB limit | after, 512 MB, 2 threads |
|---|---:|---:|---:|
| after opening the search | 83 MB | 140 MB | 83 MB |
| after all the queries | 183 MB (peak 815 MB) | 648 MB | 621 MB (peak 645 MB) |

Before, DuckDB was allowed 80% of RAM; peaks grow with the data and the query (the 815 MB peak
was on 12M rows). After, DuckDB keeps its buffer under the limit (what stays resident is mostly
cached column blocks, which make the next query fast) and spills to disk beyond it.

Building the index (once per download, in the download job):

| | 12.0M rows, 8 files |
|---|---|
| time (4 threads, build memory 2 GB) | 81 s for the 8 files and the combined key table; the FR file (7M rows) alone 50 s at a 1 GB limit |
| peak resident memory | 2.0 GB at the 2 GB build limit; ~1.2 GB at a 1 GB limit (sorts run in chunks, see the 30M run) |
| disk | 1.16 GB of indexes + 279 MB combined key table, next to 563 MB of Parquet |

So a downloaded country takes about 3.5x its Parquet size on disk in total (Parquet plus index).

## 30M rows (the "30M+ in total" case)

Same benchmark on `generate --rows 30000000` (1.4 GB of Parquet; FR alone 17.4M rows, more than
the largest real country), with the limits of a 16 GB laptop: search memory 1 GB, build memory
1 GB, 4 threads.

| query path | p50 | p95 |
|---|---:|---:|
| results page (count + 25 rows) | 80 ms | 582 ms |
| results page: browse | 62 ms | 166 ms |
| results page: country | 13 ms | 63 ms |
| results page: country+industry+size | 19 ms | 54 ms |
| results page: region/city | 300 ms | 745 ms |
| results page: text | 465 ms | 587 ms |
| results page: keywords | 355 ms | 582 ms |
| facets (3 lists) | 6 ms | 319 ms |
| full /companies request | 291 ms | 764 ms |
| sorted by name (scan 5,000) | 234 ms | 376 ms |
| get_company(id) | 8 ms | 11 ms |
| lookup by domain | 17 ms | 20 ms |
| lookup by registry number | 9 ms | 93 ms |
| place suggestions | 8 ms | 11 ms |

Build: 230 s for the 8 files and the combined key table, peak resident memory 1.17 GB (the sorts
run in chunks, so the peak follows the limit, not the data). Search memory: 1.15 GB resident after
the queries at the 1 GB limit. Disk: 3.6 GB of indexes (FR 1.7 GB, combined keys 0.7 GB) next to
the 1.4 GB of Parquet.

## Storage design

Each pack file gets a DuckDB database in `<data_dir>/.index/`, built right after the download
(`index.build_index`), named after the pack file and a signature of it (name, size, mtime, index
version). A changed pack never reuses an old index, and a running app keeps reading the old index
while a new one is built under a temporary name and renamed into place. The Parquet file stays
the source of truth (it can be re-indexed after an app update without downloading again).

Tables in each index:

- `companies`: every pack column plus `rk` and `txt`, **stored in the default result order**
  (domain first, then size known, then lower-cased name, then id); `rk` is the position. The first
  page of any search is "the 25 smallest rk that match": DuckDB's Top-N stops early and its min/max
  zone maps on rk skip row groups, so browse and structured filters read a few row groups instead
  of sorting millions of rows. `txt` = lower-cased name, legal name and domain joined by U+001F:
  free text is one `contains()` over a native column (no `lower()`, no `LIKE ... ESCAPE` per
  row), and a term cannot match across two fields.
- `keys` (kind, value, rk), sorted: lookups by id, domain and registry number (company number,
  SIREN, CIK, LEI; spaces, dots and dashes ignored) read one row group through zone maps.
- `cube`: counts per country, region, industry and size (a few thousand rows per country). The
  result count and the filter-panel counts for those filters never scan the companies.
- `places`: counts per city and region (place suggestions, the city list).
- `meta`: what the index was built from; a JSON copy next to the file is what the app reads to
  decide whether an index is fresh, without opening it.

With two or more indexed packs, `keys-<hash>.duckdb` holds one key table over all of them, so a
lookup is one query whatever the number of installed countries (per-pack lookups cost ~1.5 ms
each: 8 countries took 14 ms, 30 would take ~45 ms).

Why these choices (prototype numbers on the 7M-row FR file, 4 threads):

| question | measured | decision |
|---|---|---|
| Parquet views or native tables? | text count 1,700 ms on Parquet, 41 ms on a DuckDB table | native tables |
| sort per query or store sorted? | Top-N over a UNION view sorting by expressions 737 ms, by stored columns 87 ms, per-pack `ORDER BY rk LIMIT k` then merge 16 ms | stored order + per-pack Top-N |
| ART indexes or sorted key table? | ART on id + domain + number: +666 MB on disk, 1.5-3.7 ms lookups; sorted key table: +156 MB, 2-3 ms | sorted key table |
| zstd for text columns? | zstd on all display columns: 925 MB (sparse columns waste a block per row group); only on `sources_json` and `address`: 724 MB; none: 810 MB | zstd on the two dense display columns |
| DuckDB block size? | 256 KB: cold row fetch 26 ms, 723 MB; 64 KB: 7.7 ms, 683 MB, same scan speed, build 1.6x longer | 64 KB |
| one sort or chunks? | a 65.8M-key sort (30M companies) ran out of memory at a 2 GB limit: a DuckDB sort needs memory per row | big sorts run in consecutive ranges (2M companies, 6M keys; split points from a sample), appended in order |
| two sorts (`row_number() OVER (ORDER BY ...)` + `ORDER BY`) or one? | 72 s vs 38 s for 7M rows | one sort, numbered by a streaming `row_number() OVER ()`, checked on a sample |
| pre-normalised lower-case city/region/industry columns? | `lower(city) IN` 28 ms vs a stored `city_lc` 20 ms on 7M rows (DuckDB works on the dictionary) | not stored (smaller file) |

Packs without an index are still searched: up to 300,000 rows they are loaded into the same
structure in memory when the search opens (the bundled sample, packs built with
`pepite pack build`); larger ones are read through plain Parquet views (correct, slow) and
reported by `DuckDBCompanySearch.needs_index`, so Settings > Data packs offers "Prepare for fast
search" and the desktop app builds them in the background at start-up.

## Query paths

| UI need | how it is answered |
|---|---|
| results page (filters + order + 25 rows) | per pack: `WHERE ... ORDER BY rk LIMIT offset+25`, merged in the same order; packs of other countries are skipped |
| result count | the cubes for country / region / industry / size filters; with text, keywords or domains a "mini cube" (the matching rows grouped once by country, region, industry, size) answers the count and the facets; otherwise one count scan; cached per search object |
| filter panel counts | the cubes, or the mini cube of the text filter (one scan instead of three), else a scan; cached |
| sort by name / size, tabs | the first 5,000 rows are read once (one query) and the following pages served from memory |
| get_company, lookups by domain or registry number | the combined key table, then a row fetch by rk; `get_companies(ids)` does many ids in one query |
| domains filter (lookalikes, find company by domain) | key table first, then `rk IN (...)` |
| place suggestions | the places tables |

Caches (counts, facets, mini cubes, scan windows) live in the search object: a download or a
removal creates a new search object, so they never show old data.

## Memory and threads

`index.configure` sets, for searching: `memory_limit` = PEPITE_DUCKDB_MEMORY or 1/16 of
RAM between 256 MB and 1 GB (512 MB on an 8 GB laptop, 1 GB from 16 GB); `threads` =
PEPITE_DUCKDB_THREADS or the number of cores up to 8; `temp_directory` in `.index/tmp`
(DuckDB's default `.tmp` in the working directory is read-only inside an installed app). Building
an index uses PEPITE_DUCKDB_BUILD_MEMORY or 1/6 of RAM between 512 MB and 2 GB, and spills to
disk beyond it.

## What remains slow

- **City filters** scan the city column of every matching pack (`lower(city) IN ...`): results
  page p95 285 ms here (512 ms on 2 threads), and the filter panel counts for a city filter scan
  too (~130 ms). A cube that includes the city, or resolving the typed city to its spellings
  through the `places` table, would make them instant.
- **Free text** is a substring scan of `txt` (no word index: DuckDB's full-text extension is
  downloaded at runtime, which the offline desktop app cannot rely on). It grows linearly with the
  rows of the countries searched: ~0.2 s per 12M rows on 4 threads, ~0.4 s on 2 (~0.45 s for
  30M rows on 4 threads).
- **Cold row fetches**: `get_company` for a company whose data is not cached reads one 64 KB block
  per column (~10-20 ms). Pages that call it in a loop for many saved companies would be faster
  with `get_companies(ids)`, which fetches many in one query (not used by the pages yet).
- **Deep pages** cost more: page N reads the first N x 25 rows of each pack (page 40: ~90 ms).
- **Index build** takes about 50 s per 7M rows on 4 threads (230 s for 30M rows) and stays
  within the build memory limit (sorted in chunks, spilling beyond it); it runs once per
  download, with progress shown in the jobs menu.
- **Disk**: the index takes 2-2.5x the Parquet size (3.6 GB of indexes for 30M rows). The
  Parquet file is kept so a new index format can be built without downloading again; deleting it
  after indexing would save ~30%.
- Packs over 300,000 rows that have no index yet (copied by hand, or an index format change after
  an update) are searched through Parquet views at the old speed until their index is built (the
  desktop app starts that by itself; Settings > Data packs offers a button).
