# Tester wrapper

A tiny web UI that demonstrates the TikTok DM bridge. It is a *client* of the
bridge's HTTP API (in `bridge/webapp.py`), not part of the bridge: it serves the
static UI (`static/`) and proxies `/api/*` and `/metrics` to the bridge so the
browser stays same-origin.

Run everything with one command from the repo root:

```sh
./bridge-app.sh
```

Views: **Connect** (security explainer + real TikTok login in a local browser),
**Chats** (the connected account's real conversations, live), **Health** (the
Live-Session-Ratio metric + leading indicators). Log out wipes the session,
messages, contacts and browser profile.
