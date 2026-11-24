#!/usr/bin/env bash
# Entry point of the site scan container: download the newest hosts release, scan, build the shard.
set -euo pipefail

TLDS="${TLDS:-fr}"
PROCS="${PROCS:-2}"
CONCURRENCY="${CONCURRENCY:-60}"
DELAY="${DELAY:-1.0}"
MAX_PAGES="${MAX_PAGES:-4}"
HOSTS_REPO="${HOSTS_REPO:-aymaneprojects/leadcommons}"
HOSTS_TAG="${HOSTS_TAG:-}"
LIMIT="${LIMIT:-}"
MONTH="${MONTH:-$(date -u +%Y-%m)}"
DATA="${DATA:-/data}"

findings="$DATA/findings/$MONTH"
mkdir -p "$findings" "$DATA/hosts" "$DATA/shards/$MONTH"

# 1. The newest sitescan-hosts-* release (or HOSTS_TAG): download the domain lists we need.
tag=$(python - "$HOSTS_REPO" "$HOSTS_TAG" "$DATA/hosts" "$TLDS" <<'PY'
import json, sys, urllib.request
from pathlib import Path

repo, want, dest, tlds = sys.argv[1], sys.argv[2], Path(sys.argv[3]), sys.argv[4].split()
req = urllib.request.Request(
    f"https://api.github.com/repos/{repo}/releases?per_page=30",
    headers={"User-Agent": "PepiteBot-deploy", "Accept": "application/vnd.github+json"},
)
releases = json.load(urllib.request.urlopen(req, timeout=60))
hosts = [r for r in releases if r["tag_name"].startswith("sitescan-hosts-")]
chosen = next((r for r in hosts if not want or r["tag_name"] == want), None)
if chosen is None:
    sys.exit("no sitescan-hosts release found")
tag = chosen["tag_name"]
out = dest / tag
out.mkdir(parents=True, exist_ok=True)
assets = {a["name"]: a["browser_download_url"] for a in chosen["assets"]}
for tld in tlds:
    name = f"domains-{tld}.txt.gz"
    if name not in assets:
        sys.exit(f"{tag} has no {name}")
    target = out / name
    if not target.exists():
        urllib.request.urlretrieve(assets[name], target.with_suffix(".part"))
        target.with_suffix(".part").rename(target)
print(tag)
PY
)
echo "hosts release: $tag" >&2

# 2. Scan: PROCS processes per TLD, each its own slice of the domains (resumable).
pids=()
for tld in $TLDS; do
  for ((k = 0; k < PROCS; k++)); do
    args=(sitescan run --domains "$DATA/hosts/$tag/domains-$tld.txt.gz" --out "$findings/$tld"
      --concurrency "$CONCURRENCY" --delay "$DELAY" --max-pages "$MAX_PAGES" --part "$k/$PROCS")
    if [ -n "$LIMIT" ]; then args+=(--limit "$LIMIT"); fi
    pepite "${args[@]}" > "$findings/$tld.part-$k.stats.json" &
    pids+=("$!")
  done
done
status=0
for pid in "${pids[@]}"; do wait "$pid" || status=1; done

# 3. The shard for `pepite pack merge` (also built after a failure: whatever was read counts).
pepite sitescan shard "$findings" --out "$DATA/shards/$MONTH"
echo "done: $DATA/shards/$MONTH/sitescan.parquet" >&2
exit "$status"
