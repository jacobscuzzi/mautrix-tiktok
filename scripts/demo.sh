#!/bin/sh
# End-to-end demo: start the API, log in, sync, dump threads/events + /metrics,
# and write docs/demo-transcript.md. Fixture-backed (reproducible, no TikTok login);
# on the server, point the provider factory at a live imported session for G4.
set -e
cd "$(dirname "$0")/.."
exec .venv/bin/python -m bridge.cmd.demo "$@"
