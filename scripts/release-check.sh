#!/bin/sh
# Check that a release is ready and write its notes (the CHANGELOG.md section of that version).
# Usage: sh scripts/release-check.sh 0.2.0 [notes-file]   (run from the repository root)
set -eu

v="${1:-}"
notes="${2:-/dev/null}"
die() { printf '::error::%s\n' "$*" >&2; exit 1; }

printf '%s' "$v" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$' \
  || die "version must look like 1.2.3 (got '$v')"

check() { [ "$2" = "$v" ] || die "$1 says '$2', expected '$v'"; }
check pyproject.toml "$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml | head -n 1)"
check src/pepite/__init__.py "$(sed -n 's/^__version__ = "\(.*\)"/\1/p' src/pepite/__init__.py)"
js='import json,sys; d=json.load(open(sys.argv[1])); print(d["version"] if sys.argv[2]=="top" else d["packages"][""]["version"])'
check desktop/electron/package.json "$(python3 -c "$js" desktop/electron/package.json top)"
check desktop/electron/package-lock.json "$(python3 -c "$js" desktop/electron/package-lock.json top)"
check "desktop/electron/package-lock.json (packages)" \
  "$(python3 -c "$js" desktop/electron/package-lock.json pkg)"

tmp=$(mktemp)
awk -v v="$v" '$1 == "##" && $2 == v { f = 1; next } /^## / { f = 0 } f' CHANGELOG.md >"$tmp"
grep -q '[^[:space:]]' "$tmp" || die "CHANGELOG.md has no '## $v' section with notes"
cat "$tmp" >"$notes"
rm -f "$tmp"
echo "Release $v is consistent."
