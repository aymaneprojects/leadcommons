# Privacy

## Where data lives

- Company packs: Parquet files on your machine, built from open data.
- People, emails, lists, saved searches and the suppression list: your own database only (SQLite in `LEADCOMMONS_HOME`, or your own Postgres in server mode).
- LeadCommons has no cloud service and sends no telemetry. The only outgoing requests are the ones you trigger: downloading packs from GitHub, reading public company pages, DNS and optional SMTP checks.

## Opt-out

The app serves a public `/opt-out` page where a person can ask that an address or domain is never returned. Entries go to the suppression list, stored as SHA-256 hashes of the lowercase address or domain, so the list itself does not reveal addresses. You can add entries yourself with `leadcommons optout someone@example.com`.

If you host LeadCommons for others, put a working contact address on your deployment and answer opt-out and erasure requests promptly.

## Your responsibilities

You decide who to contact. B2B prospecting is regulated differently across countries (GDPR legitimate interest, ePrivacy, CAN-SPAM, CASL). Contact professionals about relevant professional offers, identify yourself, make unsubscribing easy and honour opt-outs.

## Crawling

The crawler respects robots.txt, identifies itself with a clear user agent, rate-limits requests, and never logs in to a site.
