"""Technology fingerprints: HTML, script URLs, meta, headers, cookies and DNS."""

from __future__ import annotations

from pathlib import Path

from pepite.enrich.crawl import Page
from pepite.insights import techrules, techs

FIXTURES = Path(__file__).parent / "fixtures" / "insights"


def names(found) -> dict[str, techs.Technology]:
    return {t.name: t for t in found}


def test_rule_table_is_large_and_unique() -> None:
    all_names = [r.name for r in techrules.RULES]
    assert len(set(all_names)) == len(all_names) >= 150
    cats = {r.category for r in techrules.RULES}
    for wanted in (
        techrules.CMS,
        techrules.SHOP,
        techrules.ANALYTICS,
        techrules.TAGS,
        techrules.MARKETING,
        techrules.CHAT,
        techrules.PAY,
        techrules.JS,
        techrules.CDN,
        techrules.ABTEST,
        techrules.CONSENT,
        techrules.EMAIL,
    ):
        assert wanted in cats


def test_homepage_fixture() -> None:
    html = (FIXTURES / "home.html").read_text(encoding="utf-8")
    found = names(techs.detect([html]))
    assert found["WordPress"].source in {"script", "html", "meta"}
    assert found["WordPress"].category == techrules.CMS
    assert found["Google Tag Manager"].evidence.startswith(
        "script src: https://www.googletagmanager"
    )
    assert "Google Analytics" in found  # gtag/js?id=G-...
    assert found["HubSpot"].category == techrules.MARKETING
    assert "Stripe" in found and "OneTrust" in found and "Google Fonts" in found
    assert found["Intercom"].source == "html"
    assert "page source:" in found["Intercom"].evidence
    assert "Meta Pixel" in found
    assert "Shopify" not in found and "Wix" not in found


def test_meta_generator_evidence() -> None:
    found = names(
        techs.detect(['<meta name="generator" content="Drupal 10 (https://drupal.org)">'])
    )
    assert found["Drupal"].source == "meta"
    assert found["Drupal"].evidence == "meta generator: Drupal 10 (https://drupal.org)"


def test_headers_and_cookies() -> None:
    found = names(
        techs.detect(
            ["<html></html>"],
            headers={"Server": "cloudflare", "CF-Ray": "abc", "X-Powered-By": "PHP/8.2.1"},
            cookies=["_ga", "PHPSESSID", "OptanonConsent", "__cf_bm"],
        )
    )
    assert found["Cloudflare"].source == "header"
    assert found["Cloudflare"].evidence == "header server: cloudflare"
    assert found["PHP"].evidence == "header x-powered-by: PHP/8.2.1"
    assert found["Google Analytics"].source == "cookie"
    assert found["Google Analytics"].evidence == "cookie: _ga"
    assert found["OneTrust"].evidence == "cookie: OptanonConsent"


def test_header_presence_only() -> None:
    found = names(techs.detect([""], headers={"x-vercel-id": "fra1::abc", "x-nf-request-id": ""}))
    assert {"Vercel", "Netlify"} <= set(found)
    assert found["Vercel"].evidence == "header x-vercel-id: fra1::abc"


def test_dns_email_providers_and_tools() -> None:
    found = names(
        techs.detect(
            [],
            dns_records={
                "mx": ["aspmx.l.google.com.", "alt1.aspmx.l.google.com."],
                "txt": [
                    "v=spf1 include:_spf.google.com include:sendgrid.net "
                    "include:mail.zendesk.com ~all",
                    "atlassian-domain-verification=xyz",
                    "google-site-verification=abc",
                ],
            },
        )
    )
    assert found["Google Workspace"].source == "dns"
    assert found["Google Workspace"].evidence.startswith("MX record: aspmx.l.google.com")
    assert found["SendGrid"].category == techrules.EMAIL
    assert "Zendesk email" in found and "Atlassian" in found and "Google Search Console" in found
    assert "Microsoft 365" not in found


def test_microsoft_365_mx() -> None:
    found = names(techs.detect([], dns_records={"mx": ["acme-com.mail.protection.outlook.com."]}))
    assert "Microsoft 365" in found


def test_ecommerce_and_frameworks() -> None:
    html = (
        '<html><head><script src="https://cdn.shopify.com/s/files/x.js"></script>'
        '<script src="/_next/static/chunks/main.js"></script></head>'
        '<body><div id="__next"></div></body></html>'
    )
    found = names(techs.detect([html], cookies=["_shopify_y"]))
    assert found["Shopify"].category == techrules.SHOP
    assert found["Next.js"].category == techrules.JS
    assert "React" not in found


def test_page_objects_accepted_as_html_strings() -> None:
    page = Page("https://x.example/", '<script src="https://js.stripe.com/v3/"></script>')
    assert "Stripe" in names(techs.detect([page.html]))


def test_results_sorted_and_deduplicated() -> None:
    html = '<script src="https://js.stripe.com/v3/"></script>' * 3
    found = techs.detect([html, html])
    assert [t.name for t in found].count("Stripe") == 1
    assert [(t.category, t.name.lower()) for t in found] == sorted(
        (t.category, t.name.lower()) for t in found
    )
