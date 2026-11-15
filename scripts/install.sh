#!/bin/sh
# LeadCommons installer:  curl -fsSL https://raw.githubusercontent.com/aymaneprojects/leadcommons/main/scripts/install.sh | sh
# Env overrides: LEADCOMMONS_REPO (owner/name), LEADCOMMONS_VERSION (tag, branch or commit).
set -eu

REPO="${LEADCOMMONS_REPO:-aymaneprojects/leadcommons}"
VERSION="${LEADCOMMONS_VERSION:-}"

say() { printf '%s\n' "$*"; }
warn() { printf 'Warning: %s\n' "$*" >&2; }
die() { printf 'Error: %s\n' "$*" >&2; exit 1; }

command -v curl >/dev/null 2>&1 || die "curl is required."

# 1. uv (Python tool installer)
if ! command -v uv >/dev/null 2>&1; then
  say "Installing uv (https://astral.sh/uv) ..."
  curl -LsSf https://astral.sh/uv/install.sh | sh || die "could not install uv."
  PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
  export PATH
  command -v uv >/dev/null 2>&1 || die "uv was installed but is not on PATH; open a new shell and run this script again."
fi

# 2. Which version?
if [ -z "$VERSION" ]; then
  say "Looking for the latest release of $REPO ..."
  # Only app releases (tags v1.2.3); the monthly data packs are releases too (packs-YYYY-MM).
  VERSION=$(curl -fsSL "https://api.github.com/repos/$REPO/releases?per_page=30" 2>/dev/null \
    | sed -n 's/.*"tag_name"[[:space:]]*:[[:space:]]*"\(v[0-9][^"]*\)".*/\1/p' | head -n 1 || true)
  if [ -z "$VERSION" ]; then
    warn "no release found, using the main branch."
    VERSION=main
  fi
fi
say "Installing LeadCommons $VERSION ..."

# 3. Install
uv tool install --force --python 3.13 "leadcommons @ git+https://github.com/$REPO@$VERSION" \
  || die "installation failed."

# uv puts tools in this directory
TOOL_BIN=$(uv tool dir --bin 2>/dev/null || echo "$HOME/.local/bin")
PATH="$TOOL_BIN:$PATH"
export PATH

# 4. Company data (not fatal: the app ships with a small sample)
say "Downloading the latest company data pack ..."
LEADCOMMONS_PACKS_REPO="${LEADCOMMONS_PACKS_REPO:-$REPO}" leadcommons pack download latest \
  || warn "could not download a pack; the bundled sample will be used. Retry later with: leadcommons pack download latest"

say ""
say "LeadCommons is installed."
say "Start it with:   leadcommons serve"
say "Then open:       http://127.0.0.1:8000"
say "If 'leadcommons' is not found, run: uv tool update-shell   (then open a new terminal)"
