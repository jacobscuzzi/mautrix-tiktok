#!/usr/bin/env bash
# Install everything the bridge needs: the venv, Python deps, and the Chromium
# browser. Idempotent and safe to re-run; only installs what is missing. Called
# automatically by bridge-app.sh, or run it once by hand on a fresh clone.
set -eu
cd "$(dirname "$0")"
PY=".venv/bin/python"
say() { printf '  \033[36m%s\033[0m\n' "$*"; }

if [ ! -x "$PY" ]; then
  say "creating virtual environment (.venv)…"
  python3 -m venv .venv 2>/dev/null || python3 -m venv .venv --without-pip
fi
if ! "$PY" -m pip --version >/dev/null 2>&1; then
  say "bootstrapping pip…"
  GETPIP="$(mktemp)"
  curl -fsSL https://bootstrap.pypa.io/get-pip.py -o "$GETPIP"
  "$PY" "$GETPIP" >/dev/null
  rm -f "$GETPIP"
fi
if ! "$PY" -c "import playwright, cryptography, yaml, requests" >/dev/null 2>&1; then
  say "installing Python dependencies…"
  "$PY" -m pip install -q --upgrade pip
  "$PY" -m pip install -q -r requirements.txt
fi
if ! "$PY" - <<'PYCHK' >/dev/null 2>&1
# ask Playwright where its Chromium is (Linux, macOS and Windows layouts differ)
import os
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    raise SystemExit(0 if os.path.exists(p.chromium.executable_path) else 1)
PYCHK
then
  say "installing the Chromium browser (first run only)…"
  if command -v sudo >/dev/null 2>&1 && sudo -n true 2>/dev/null; then
    say "installing Chromium's system libraries with sudo apt (playwright --with-deps)…"
    "$PY" -m playwright install --with-deps chromium || "$PY" -m playwright install chromium
  else
    "$PY" -m playwright install chromium
  fi
fi
say "setup complete."
