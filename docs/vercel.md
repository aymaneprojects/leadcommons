# Public demo on Vercel

Pépite deploys to Vercel's Python runtime without changes:

- `vercel_app.py` is the entrypoint (`[tool.vercel] entrypoint = "vercel_app:app"` in `pyproject.toml`).
- The build step `scripts/vercel_build.py` downloads the small all-countries demo file of the newest
  published company data (a `packs-*` GitHub Release; the function has a size limit, so it is not
  the full per-country data) into `data/packs/`. It reads the repository in `PEPITE_PACKS_REPO`.
  Without a published release, the bundled sample pack is used.
- `vercel.json` sets the function timeout (60 seconds) and leaves tests, docs and deployment files out of the bundle.

## Demo mode

On Vercel (`VERCEL=1`) the app defaults to `PEPITE_MODE=demo`:

- no login, company search, profiles, lists and exports work;
- finding people, finding emails and CSV enrichment are refused with a clear message, so the
  deployment is never used to crawl websites or check email addresses for anonymous visitors;
- a banner explains this on every page.

Serverless functions have no permanent disk: the database lives in `/tmp` and is reset when an
instance restarts, so lists saved on the demo are temporary and shared by visitors of the same
instance. Jobs run inside the request (`PEPITE_SYNC_JOBS`), because background threads are
frozen once a response is sent.

## Deploy your own

1. Import the repository in Vercel (framework preset: FastAPI is detected).
2. Optionally set `PEPITE_MODE=demo` explicitly (it is already the default on Vercel).
3. Every push to the production branch redeploys.

A private, full-featured instance needs a permanent database and background work: use the Docker
setup in [server.md](server.md) instead.
