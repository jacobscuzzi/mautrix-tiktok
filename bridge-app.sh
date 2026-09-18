#!/usr/bin/env bash
# One command to run the TikTok DM bridge + tester wrapper. Installs everything it
# needs on the first run (venv, deps, Chromium), then starts the app and opens your
# browser. Re-running is fast.
set -euo pipefail
cd "$(dirname "$0")"
./setup.sh
if [ -f .chromium-libs/ldpath.txt ]; then
  export LD_LIBRARY_PATH="$(cat .chromium-libs/ldpath.txt)"
fi
printf '  \033[36m%s\033[0m\n' "starting the bridge…"
exec .venv/bin/python -m bridge.cmd.serve "$@"
