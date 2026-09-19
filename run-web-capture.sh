#!/bin/sh
# Live logged-in TikTok web capture -> browser-data/<user>/
#
#   ./run-web-capture.sh --mode manual --headful --user <name>
#
# Needs the venv with Playwright + Chromium (setup.sh). On a server without the
# system libraries the shared libs come from .chromium-libs/ (LD_LIBRARY_PATH); on a
# laptop with a GUI use --headful so you can log in and trigger a DM. --allow-send
# also records one outgoing DM; it is off by default.
set -e
cd "$(dirname "$0")"
if [ -f .chromium-libs/ldpath.txt ]; then
  export LD_LIBRARY_PATH="$(cat .chromium-libs/ldpath.txt)"
fi
exec .venv/bin/python -m bridge.cmd.capture "$@"
