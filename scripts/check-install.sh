#!/bin/sh
# Check an installed Pepite: version, help, then start it and fetch the main pages.
# Usage: PEPITE_HOME=<throwaway dir> sh scripts/check-install.sh [port]
set -eu

PORT="${1:-18765}"
BASE="http://127.0.0.1:$PORT"
TMP="${RUNNER_TEMP:-${TMPDIR:-/tmp}}"

command -v pepite
pepite version
pepite --help >/dev/null
pepite serve --port "$PORT" >"$TMP/serve.log" 2>&1 &
trap 'kill $! 2>/dev/null || true' EXIT

up=0
for _ in $(seq 1 90); do
  if curl -fsS "$BASE/healthz" >/dev/null 2>&1; then up=1; break; fi
  sleep 1
done
if [ "$up" != 1 ]; then echo "The app did not start."; cat "$TMP/serve.log"; exit 1; fi

fail=0
check() {
  code=$(curl -s -o "$TMP/body" -w '%{http_code}' "$BASE$1" || true)
  if [ "$code" = 200 ] && grep -q -- "$2" "$TMP/body"; then echo "ok   $1"
  else echo "FAIL $1 (HTTP $code, looked for: $2)"; fail=1; fi
}
check /healthz '"ok"'
check / 'Pépite'
check '/companies?all=1' 'co-name'
check /people 'Pépite'
check /settings/data-packs 'Data packs'
check /static/css/app.css '--orange'
if [ "$fail" != 0 ]; then cat "$TMP/serve.log"; exit 1; fi
echo "Install check passed."
