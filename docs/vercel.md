# Public demo on Vercel

LeadCommons deploys to Vercel's Python runtime without changes:

- `vercel_app.py` is the entrypoint (`[tool.vercel] entrypoint = "vercel_app:app"` in `pyproject.toml`).
- The build step `scripts/vercel_build.py` downloads the newest published company pack
  (a `packs-*` GitHub Release) into `data/packs/`; without one, the bundled sample pack is used.
- `vercel.json` sets the function timeout and leaves tests, docs and deployment files out of the bundle.

## Demo mode

On Vercel (`VERCEL=1`) the app defaults to `LEADCOMMONS_MODE=demo`:

- no login, company search, profiles, lists and exports work;
- finding people, finding emails and CSV enrichment are refused with a clear message, so the
  deployment is never used to crawl websites or check email addresses for anonymous visitors;
- a banner explains this on every page.

Serverless functions have no permanent disk: the database lives in `/tmp` and is reset when an
instance restarts, so lists saved on the demo are temporary and shared by visitors of the same
instance. Jobs run inside the request (`LEADCOMMONS_SYNC_JOBS`), because background threads are
frozen once a response is sent.

## Deploy your own

1. Import the repository in Vercel (framework preset: FastAPI is detected).
2. Optionally set `LEADCOMMONS_MODE=demo` explicitly (it is already the default on Vercel).
3. Every push to the production branch redeploys.

A private, full-featured instance needs a permanent database and background work: use the Docker
setup in [server.md](server.md) instead.
