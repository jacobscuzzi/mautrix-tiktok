#!/bin/sh
# End-to-end demo against fixtures: starts the API, logs in, syncs, prints
# threads/events + /metrics and writes demo-transcript.md (gitignored). No TikTok login.
set -e
cd "$(dirname "$0")/.."
exec .venv/bin/python -m bridge.cmd.demo "$@"
