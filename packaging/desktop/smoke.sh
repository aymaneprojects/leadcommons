#!/usr/bin/env bash
# Start a built desktop app without a window and check that it serves the main pages.
# Usage: packaging/desktop/smoke.sh <path to LeadCommons binary or AppImage> [port]
# Set LEADCOMMONS_HOME to a throwaway folder first.
set -euo pipefail

BIN="$1"
PORT="${2:-8765}"
BASE="http://127.0.0.1:$PORT"
OUT="$(mktemp)"
export LEADCOMMONS_DESKTOP_NO_DOWNLOAD=1

"$BIN" --headless --port "$PORT" --exit-after 150 >"$OUT" 2>&1 &
PID=$!

show_log() {
  echo "--- app output"; cat "$OUT" || true
  echo "--- desktop.log"; cat "${LEADCOMMONS_HOME:-$HOME/.leadcommons}/desktop.log" || true
}

for _ in $(seq 1 120); do
  if curl -fsS "$BASE/healthz" >/dev/null 2>&1; then break; fi
  if ! kill -0 "$PID" 2>/dev/null; then echo "The app exited before serving."; show_log; exit 1; fi
  sleep 1
done

fail=0
check() {
  local path="$1" marker="$2" body code
  body="$(mktemp)"
  code="$(curl -s -o "$body" -w '%{http_code}' "$BASE$path" || true)"
  if [ "$code" = "200" ] && grep -q -- "$marker" "$body"; then
    echo "ok   $path"
  else
    echo "FAIL $path (HTTP $code, looked for: $marker)"; fail=1
  fi
}

check /healthz '"ok"'
check "/companies?all=1" 'co-name'
check "/people" 'LeadCommons'
check "/settings/data-packs" 'Data packs'
check "/api/docs" 'API reference'
check "/static/css/app.css" '--orange'
check "/static/vendor/htmx.min.js" 'htmx'

kill "$PID" 2>/dev/null || true
if [ "$fail" != 0 ]; then show_log; exit 1; fi
echo "Smoke test passed for $BIN"
