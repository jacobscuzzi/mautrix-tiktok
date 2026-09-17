#!/bin/sh
# One command to finish the TikTok web bridge login + DM capture on this server.
# Requires the venv + SignerPy + Chromium libs already set up (they are).
set -e
cd "$(dirname "$0")"
export LD_LIBRARY_PATH="$(cat .chromium-libs/ldpath.txt)"
export QR_MAX_WAIT="${QR_MAX_WAIT:-900}"

echo "1/2  Launching QR login. A QR will appear at browser-data/qr.png in a few seconds."
echo "     Scan it with your TikTok app (Profile > menu > scan) and approve."
.venv/bin/python -m bridge.auth.web_qr_login browser-data

if [ ! -f browser-data/session.json ]; then
  echo "No session captured (QR not scanned in time). Re-run to get a fresh QR."
  exit 1
fi

echo "2/2  Session captured. Opening web Messages to map the DM API..."
WATCH="${WATCH:-90}" .venv/bin/python -m bridge.auth.web_dm_capture browser-data
echo "Done. See browser-data/dm_capture.jsonl and browser-data/messages.png"
