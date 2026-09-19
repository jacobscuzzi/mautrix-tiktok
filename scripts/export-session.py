#!/usr/bin/env python3
"""Export a captured laptop session to the server through the cookies flow.

  .venv/bin/python scripts/export-session.py --user <name> --to https://server/v1

Reads browser-data/<user>/storage_state.json + meta.json, builds the cookies-import
payload, and POSTs it to the API's cookies login flow. The session is re-encrypted
on import server-side; the laptop and server master keys need not match. Plaintext
storage_state.json should be deleted once the import is confirmed.
"""
import argparse
import json
import os
import urllib.request


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", required=True)
    ap.add_argument("--to", required=True, help="API base, e.g. https://host/v1")
    ap.add_argument("--token", default=os.environ.get("BRIDGE_API_TOKEN", ""))
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    d = os.path.join("browser-data", a.user)
    state = json.load(open(os.path.join(d, "storage_state.json")))
    meta = json.load(open(os.path.join(d, "meta.json")))
    jar = {c["name"]: c["value"] for c in state.get("cookies", [])}
    payload = {
        "cookies": state.get("cookies", []),
        "local_storage": state.get("origins", []),
        "user_agent": meta.get("ua", ""),
        "device": {"ttwid": jar.get("ttwid", ""), "device_id": jar.get("uid_tt", ""),
                   "region": meta.get("region", "")},
    }
    if a.dry_run:
        print(f"would POST cookies import for {a.user}: "
              f"{len(payload['cookies'])} cookies, UA={payload['user_agent'][:40]}...")
        return

    def post(path, body):
        req = urllib.request.Request(a.to + path, data=json.dumps(body).encode(),
                                     method="POST")
        req.add_header("Content-Type", "application/json")
        if a.token:
            req.add_header("Authorization", f"Bearer {a.token}")
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())

    start = post("/login/start", {"flow": "cookies"})
    print("start:", start)
    res = post(f"/login/{start['login_id']}/step", payload)
    print("import:", res)


if __name__ == "__main__":
    main()
