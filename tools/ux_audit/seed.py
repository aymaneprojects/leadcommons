"""Seed a throwaway Pépite home with a few obviously fake people for the UX audit.

Usage: PEPITE_HOME=/tmp/x python tools/ux_audit/seed.py
"""

from __future__ import annotations

from pepite.config import Settings
from pepite.models import EmailCandidate, EmailStatus, Person, Seniority, make_id
from pepite.packs import DuckDBCompanySearch
from pepite.search import CompanyFilters
from pepite.store import Store

FAKE = [
    ("Alex Example", "Chief Executive Officer", Seniority.C_SUITE, EmailStatus.VALID, 92),
    ("Sam Sample", "Head of Sales", Seniority.HEAD, EmailStatus.CATCH_ALL, 55),
    ("Robin Test", "Marketing Manager", Seniority.MANAGER, EmailStatus.UNKNOWN, 40),
]


def main() -> None:
    settings = Settings()
    settings.ensure_dirs()
    store = Store(settings.database_url)
    search = DuckDBCompanySearch(settings.data_dir)
    companies = [
        c
        for c in search.search_companies(CompanyFilters(text="bank"), per_page=10).items
        if c.domain
    ][:3]
    for company, (name, title, seniority, status, conf) in zip(companies, FAKE, strict=False):
        store.save_company(company)
        person = Person(
            id=make_id("pe", company.domain or "", name),
            full_name=name,
            company_domain=company.domain,
            company_id=company.id,
            title=title,
            seniority=seniority,
            source_urls=[f"https://{company.domain}/team"],
        )
        store.save_person(person)
        first, last = name.lower().split()
        store.save_email(
            person.id,
            EmailCandidate(
                address=f"{first}.{last}@{company.domain}",
                status=status,
                confidence=conf,
                pattern="{first}.{last}",
            ),
        )
    lid = store.create_list("Audit list", "people")
    store.add_to_list(
        lid, [make_id("pe", c.domain or "", f[0]) for c, f in zip(companies, FAKE, strict=False)]
    )
    print(f"seeded {len(companies)} people into {settings.home}")


if __name__ == "__main__":
    main()
