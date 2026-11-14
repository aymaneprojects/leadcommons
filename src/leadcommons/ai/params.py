"""Turn parsed filters into the query-string parameters the HTML pages understand."""

from __future__ import annotations

from urllib.parse import urlencode

from leadcommons.ai.rules import Kind, ParsedQuery


def to_query_params(parsed: ParsedQuery, kind: Kind = "companies") -> list[tuple[str, str]]:
    """/companies: country, size, industry, keyword (repeated).
    /people: the same company params plus seniority (repeated) and title (comma-joined; the
    page splits on commas).
    """
    out: list[tuple[str, str]] = []
    if kind == "companies":
        c = parsed.company
        out += [("country", v) for v in c.countries]
        out += [("size", v) for v in c.size_ranges]
        out += [("industry", v) for v in c.industries]
        out += [("keyword", v) for v in c.keywords]
    else:
        p = parsed.people
        c = p.company
        out += [("country", v) for v in c.countries]
        out += [("size", v) for v in c.size_ranges]
        out += [("industry", v) for v in c.industries]
        out += [("keyword", v) for v in c.keywords]
        out += [("seniority", s.value) for s in p.seniorities]
        if p.titles:
            out.append(("title", ",".join(p.titles)))
    return out


def ignored_filters(parsed: ParsedQuery, kind: Kind = "companies") -> list[str]:
    """Understood filters that the target page has no parameter for (the /companies page
    has no job-title filters; /people accepts everything we parse)."""
    if kind != "companies":
        return []
    ignored = []
    if parsed.people.titles:
        ignored.append("title")
    if parsed.people.seniorities:
        ignored.append("seniority")
    return ignored


def to_url(parsed: ParsedQuery, kind: Kind = "companies") -> tuple[str, str]:
    """(url-encoded query string, page url)."""
    query = urlencode(to_query_params(parsed, kind))
    return query, f"/{kind}" + (f"?{query}" if query else "")
