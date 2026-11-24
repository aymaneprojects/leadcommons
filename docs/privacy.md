# Privacy

## Where data lives

- Company packs: Parquet files on your machine, built from open data. Your whole data folder is `~/.pepite` (or `PEPITE_HOME`).
- People, emails, lists, saved searches and the suppression list: your own database only (SQLite in `PEPITE_HOME`, or your own Postgres in server mode).
- Pépite has no cloud service and sends no telemetry. The only outgoing requests are the ones you trigger or ask for: downloading packs from GitHub, reading public company pages and official registers, DNS and optional SMTP checks, sending your sequence emails through your own mailbox, and the desktop app's check for a new version on GitHub (**Help > Check for updates…**, also run shortly after it starts). If you set `PEPITE_LLM_URL` and `PEPITE_LLM_MODEL`, what you type in a search, in the sequence draft box or in the Assistant, and the public text of a website you ask it to summarise, is sent to that model server. So are the rows you select when you use **Research with AI** on the results. Point it at a model you trust, or leave it unset.

## Opt-out

The app serves a public `/opt-out` page where a person can ask that an address or domain is never returned. Entries go to the suppression list, stored as SHA-256 hashes of the lowercase address or domain, so the list itself does not reveal addresses. You can add entries yourself in Settings > Privacy and opt-out, on a person or company page ("Mark ... as do not contact"), or with `pepite optout someone@example.com`. Every sequence email also has a personal unsubscribe link that adds the address to the list.

If you host Pépite for others, put a working contact address on your deployment and answer opt-out and erasure requests promptly.

## Your responsibilities

You decide who to contact. B2B prospecting is regulated differently across countries (GDPR legitimate interest, ePrivacy, CAN-SPAM, CASL). Contact professionals about relevant professional offers, identify yourself, make unsubscribing easy and honour opt-outs.

## Crawling

The crawler respects robots.txt, identifies itself with a clear user agent, rate-limits requests, and never logs in to a site.
