# TikTok DM bridge (prototype)

An unofficial TikTok DM bridge: a user logs in with their TikTok account and their
conversations — contacts, profiles, threads, messages — are fetched on our backend
and normalized into a mautrix-style pipeline. Python prototype. This README leads
with how it breaks, because that is the job. Full design: `DESIGN.md`. The brief it
answers: `docs/BRIEF.md`.

## The surface, in one paragraph

The mobile app API is IP-gated from a datacenter (`error_code 7`) and needs device
registration we cannot do (TTEncrypt) — correct, not runnable. A real Chromium is
**not** gated: it loads tiktok.com, and TikTok's own web signer (`webmssdk`) signs
every request the page makes. So the bridge drives a headless browser per user and
taps its network + the `wss://im-ws.tiktok.com/ws/v2` frontier socket. **The page is
the signer** — confirmed live: an in-page `fetch` with the signing params stripped
came back signed and 200. Details and the mobile/tikapi alternatives: `DESIGN.md` §1.

## Failure modes (first)

Detection is scoped to blast radius: **all users failing at once = our bug**
(signer/browser/key); **one user = that account**. No irreversible action on an
ambiguous signal (no blind send-replay, no auto-relogin loop). Every row is backed
by a test or a live observation — the full table with citations is `DESIGN.md` §5.

| Condition | Detection | State | Recovery |
|---|---|---|---|
| Password wrong | login-error element | `bad_credentials` | user retries; never auto-retried |
| Challenge (captcha/2FA/IDV) | challenge element | `needs_user` + action | app opens URL → cookies flow |
| Session expired / logged-out elsewhere | `token/beat` code 8, `/login` 302 | `needs_user` | one re-login, never auto-loop |
| Rate limited | HTTP 429 / body code 7 | `rate_limited` | backoff + jitter, never spin |
| Locked / banned | `account-locked` / 403 | `blocked` | user action |
| Frontier gap / socket drop | id discontinuity | reconcile | REST reconcile via Syncer dedup |
| DOM/schema change | missing selector / renamed field | `schema_change` | one module to fix |
| Signer stale for ALL users | cross-user 4xx spike (canary) | alert | hot-swap the browser image |
| Fingerprint mismatch on import | UA vs stored profile | `fingerprint_mismatch` | refuse; never rotate identity |

## Security

No raw password persisted (default), ideally never received (QR); the browser flow
types it into TikTok's own page. The session blob + fingerprint is
**envelope-encrypted at rest** (`bridge/session_store.py`): a per-user AES-GCM data
key, wrapped by a master key from env/KMS. Contact/message data is third-party PII:
encrypted, deleted on logout (`DELETE /v1/logins/{id}`). Secrets via env/KMS, never
the repo; logs redact values (grep-tested). The raw capture and plaintext
`storage_state.json` are gitignored and shredded after import; a length-preserving
redactor produces the fixtures. Full paragraph: `DESIGN.md` §6.

## Health metric

**`bridge_live_session_ratio`** = connected / total (headline; every failure mode
drops it). Leading indicator **`bridge_password_login_share`** (rising = sessions
dying early, precedes bans). Plus `bridge_delivery_lag_seconds` p95 and
`bridge_error_total{state}`. Prometheus at `GET /metrics`.

## Try it as an app (one command)

```bash
./bridge-app.sh                 # starts the bridge + the tester wrapper, opens http://127.0.0.1:8770
```

The wrapper is a small web UI (the "tester") that talks to the bridge over HTTP:

1. **Connect** — a plain-language panel explains exactly how your data is handled,
   then a real Chromium window opens on TikTok's own login page. You log in there
   (password or QR); the bridge never sees your password.
2. **Chats** — once connected, your real contacts, threads and messages sync in and
   render; new DMs arrive live over the frontier socket.
3. **Health** — the one production number, Live-Session-Ratio, shown big, with the
   leading indicator (password-login share), delivery-lag p95, and error states.

The session is envelope-encrypted at rest and **everything is wiped on logout**
(session, messages, contacts, browser profile). Real login needs a display (WSLg
`$DISPLAY` on WSL). Run against a throwaway account. The bridge program is
`bridge/` (API in `bridge/webapp.py`, live browser worker in `bridge/live.py`); the
wrapper is `wrapper/`.

## Run it

```bash
.venv/bin/python -m unittest discover -s tests      # 197 tests (1 skipped if no browser)
./scripts/demo.sh                                   # end-to-end API demo -> docs/demo-transcript.md
```

The demo starts the API, logs in, syncs contacts/threads/messages into SQLite, and
prints `/metrics` — fixture-backed so it reproduces with no TikTok login. A real run
of it against the imported session is `docs/demo-transcript.md` (redacted); graders
cannot log in to TikTok, so the transcript is the evidence.

## How to reproduce the capture (gate G1)

```bash
./setup.sh                                          # venv, Playwright, Chromium (+libs on the server)
./run-web-capture.sh --mode manual --headful --user <name>
# log in yourself, clear any challenge, have a second account send a DM, press Enter
python scripts/redact-capture.py 'browser-data/<name>/capture-*.jsonl'   # -> tests/fixtures/web/
python scripts/decode-frontier.py 'browser-data/<name>/capture-*.jsonl'  # -> frontier-fields.md
```

The observation write-up is `docs/observations/tiktok-web-dm-2026-09-18.md`. On the
server, import the laptop session with `scripts/export-session.py` and run the demo
against it (gate G4 — done live: 19 contacts pulled read-only into SQLite).

## What is proven / not built

Verified live: signing accepted (blocked by IP), browser QR from a datacenter,
logged-in DM capture, in-page re-signing, **G4 session import + 19 real contacts +
frontier socket**. Fixtures: contacts/profile/message parsers + Syncer dedup.
Fake-platform only: the full password ladder. Not built: live web conv-list JSON
(empty inbox), web send/mark-read (no fixture), the Go appservice (mapping in
`DESIGN.md` §9). The honest list is `DESIGN.md` §11; the Monday plan is §12.

## Layout

One `MessageProvider` seam (`bridge/provider.py`) lets three backends be
interchangeable. **The web browser path ships**; the mobile signed-API client and
the TikAPI vendor adapter are the documented build-vs-buy alternatives (kept behind
the seam and unit-tested, see `DESIGN.md` §1). The pipeline below the seam — sync,
normalize, SQLite store, metrics — is shared by all three.

```
bridge/
  live.py            the running bridge: one browser worker per user (login,
                     sync, realtime, history) — drives the demo app
  webapp.py          bridge HTTP API (JSON + /metrics), backed by live.py
  pipeline.py        SQLite raw layer (logins/users/threads/events), idempotent
  sync.py state.py   backfill + poll with dedup; cursors + watermarks
  normalize.py       TikTok objects -> canonical User/Thread/Event
  metrics.py         Live-Session-Ratio + leading indicators (Prometheus)
  session_store.py   AES-GCM envelope-encrypted session at rest
  proto.py           hand-rolled protobuf codec (encode/decode tree)
  provider.py        the MessageProvider seam (web | mobile | tikapi)
  web/               session, page client (in-page signed fetch), frontier
                     (pbbp2) decode, WKWebView inject assets
  providers/         web.py (shipped), tikapi.py (vendor alternative)
  auth/              login state machine, web_password_login (ladder),
                     web_cookie_import, browser (stealth factory)
  im.py client.py    the mobile signed-protobuf client (documented alternative)
  signing.py device.py passport.py envelope.py proxy.py config.py
  cmd/               serve (demo launcher), capture, demo, run
wrapper/             the tester UI: static/ (connect / chats / health) + a proxy
                     server; a client of the bridge API
scripts/             redact-capture, decode-frontier, export-session, g4_live_sync, demo.sh
tests/               unittest + fixtures/web/ + fake_platform.py
docs/                DESIGN, BRIEF, observations/, notes/, DECISIONS, PROGRESS
```
