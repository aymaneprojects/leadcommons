"""Hand-built CompanyInsights for tests and for seeding a demo database (screenshots)."""

# ruff: noqa: E501
from __future__ import annotations

from datetime import timedelta

from pepite.insights.model import (
    CompanyInsights,
    Contact,
    EmployeePoint,
    JobPosting,
    Jobs,
    Listing,
    Phone,
    RevenuePoint,
    Technology,
    Trends,
)
from pepite.models import utcnow


def sample_insights(company_id: str, domain: str = "nordwind.example") -> CompanyInsights:
    techs = [
        ("WordPress", "CMS", "meta generator: WordPress 6.4.2", "meta"),
        ("Google Tag Manager", "Tag manager", "script src: https://www.googletagmanager.com/gtm.js?id=GTM-ABC123", "script"),
        ("Google Analytics", "Analytics", "cookie: _ga", "cookie"),
        ("HubSpot", "Marketing automation", "script src: //js.hs-scripts.com/1234567.js", "script"),
        ("Intercom", "Chat and support", "page source: window.intercomSettings", "html"),
        ("Stripe", "Payments", "script src: https://js.stripe.com/v3/", "script"),
        ("Cloudflare", "CDN and hosting", "header server: cloudflare", "header"),
        ("nginx", "Web server", "header server: nginx", "header"),
        ("OneTrust", "Consent management", "script src: https://cdn.cookielaw.org/scripts/otSDKStub.js", "script"),
        ("Google Workspace", "Email provider", "MX record: aspmx.l.google.com.", "dns"),
        ("jQuery", "JavaScript framework", "script src: /wp-includes/js/jquery/jquery.min.js", "script"),
    ]  # fmt: skip
    postings = [
        JobPosting("Senior Data Engineer", "greenhouse", "https://boards.greenhouse.io/nordwind/jobs/1", "Engineering", "Hamburg, Germany", False, "2026-10-01"),
        JobPosting("Backend Engineer", "greenhouse", "https://boards.greenhouse.io/nordwind/jobs/2", "Engineering", "Remote - Europe", True, "2026-10-12"),
        JobPosting("Account Executive", "greenhouse", "https://boards.greenhouse.io/nordwind/jobs/3", "Sales", "Berlin", None, "2026-10-23"),
        JobPosting("Customer Success Manager", "greenhouse", None, "Customer Success", "Hamburg", None, None),
    ]  # fmt: skip
    return CompanyInsights(
        company_id=company_id,
        domain=domain,
        technologies=[Technology(*t) for t in techs],
        jobs=Jobs("greenhouse", "nordwind", "https://boards.greenhouse.io/nordwind", postings),
        contact=Contact(
            phones=[
                Phone("+49 40 1234567", "+49401234567", "https://nordwind.example/", None),
                Phone("040 7654321", "+49407654321", "json-ld", "Head office"),
            ],
            emails=["contact@nordwind.example", "info@nordwind.example"],
            address={"street": "Hafenstrasse 1", "postal_code": "20457", "city": "Hamburg", "country": "DE"},
            social={"linkedin": "https://www.linkedin.com/company/nordwind-data", "x": "https://x.com/nordwinddata"},
        ),
        trends=Trends(
            wikidata_id="Q42042",
            wikidata_url="https://www.wikidata.org/wiki/Q42042",
            employees=[
                EmployeePoint("2019-12-31", 640),
                EmployeePoint("2020-12-31", 810),
                EmployeePoint("2021-12-31", 990),
                EmployeePoint("2022-12-31", 1200),
                EmployeePoint("2023-12-31", 1500),
                EmployeePoint("2024-12-31", 1800),
            ],
            revenue=[RevenuePoint(2023, 250_000_000.0, "euro"), RevenuePoint(2024, 310_000_000.0, "euro")],
            listings=[Listing("Frankfurt Stock Exchange", "NWD")],
            owned_by=["Nordwind Holding"],
        ),
        pages_crawled=4,
        notes=["No supported job board is linked from the site."],
        refreshed_at=utcnow() - timedelta(hours=3),
    )  # fmt: skip
