#!/bin/sh
# One command: start the TikTok DM bridge + the tester wrapper, open the browser.
# Real TikTok login needs a display (WSLg $DISPLAY on this laptop). Sessions are
# encrypted at rest and wiped on logout.
set -e
cd "$(dirname "$0")"
if [ -f .chromium-libs/ldpath.txt ]; then
  export LD_LIBRARY_PATH="$(cat .chromium-libs/ldpath.txt)"
fi
exec .venv/bin/python -m bridge.cmd.serve "$@"
