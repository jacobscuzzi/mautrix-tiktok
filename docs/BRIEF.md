# Claude Code brief — finish the TikTok DM bridge (Knows case study, Exercise 2)

You are continuing an existing repo (branch `integration`). The design phase is over;
the remaining work is to close the gap between what is built and what the brief grades:
"anyone can login via your app (TikTok username + password), and you automatically fetch
their conversations data on your backend" — contacts, profile pictures, threads, messages.
Deliverables are a 2–3 page design doc and this repo. Graded on reasoning, failure modes
first, security paragraph, one health metric, and how far it gets — not production-readiness.

Work in the order below. Keep the test suite green and commit per task.

---

## 0. How we work together

### 0.1 Human gates
Some steps need me (Jakob) physically: a real TikTok login, a phone for 2FA, a friend
sending a DM, a headful browser. You cannot do these and you must not fake them. When you
reach one, stop and print exactly this block, then wait:

```
=== HUMAN STEP NEEDED (gate G<n>) ===
Why:        <one sentence: what is blocked without this>
Where:      laptop (headful browser) | server | phone
Do:
  1. <exact command or action>
  2. ...
Have ready: <accounts, phone, second account, ~time>
Expected:   ~<minutes>
Reply with: "done G<n>" + paste of <the specific log lines / file path I should send>
=====================================
```

Rules for gates:
- Never proceed past a gate on assumptions. While waiting, you may work on items marked
  `[parallel-ok]` below; nothing else.
- If the gate result differs from what the plan expected (login challenged, endpoint
  missing, frame undecodable), do not paper over it: log it in `docs/DECISIONS.md`, adjust
  the plan, tell me what changed.
- Small ambiguities that do not block: decide, and append one line to `docs/DECISIONS.md`
  (`date · decision · why · how to reverse`). Summarize new lines at every gate.

Known gates: G0 environment + deadline (Task 0) · G1 run the capture (Task A) · G2 approve
the observation doc (Task A) · G3 live password login (Task C) · G4 live end-to-end on the
server (Task D) · G5 final report (Task E).

### 0.2 Two environments
Detect which one you are on at start (`uname -a`, `id -u`, `echo $DISPLAY`, `which sudo`,
`python3 --version`) and say so:
- **Server:** Python 3.14, no root, headless-shell Chromium via `.chromium-libs/`, network
  reachable, EU egress `[Obs]`. Where sync and the API run. No headful browser.
- **Laptop:** Windows + WSL Ubuntu + VSCode Remote, sudo available. WSLg may provide
  `$DISPLAY=:0` for a headful window; if not, the capture must also run from native Windows
  Python. Every human-gate tool must work from a fresh clone on the laptop:
  `./setup-local.sh` (venv, `playwright install chromium` with `--with-deps` when sudo works,
  prefer `channel="chrome"` if a real Chrome is installed).
The encrypted session travels laptop → server through the `cookies` import in Task C/D.
Master key handling for that transfer is documented in Task D; never copy plaintext
`storage_state.json` between machines.

### 0.3 Time budget and priority
Ask at G0 for the hard deadline. Budgets: Task 0 ≤ 30 min · A ≤ 2 h build + gate · B ≤ 4 h ·
C ≤ 3 h · D ≤ 3 h · E ≤ 3 h · F ≤ 2 h. If a task exceeds budget: commit what is green, write
the gap into `FINISH-HERE.md`, move on.
If the deadline forces cuts, this is the order things get dropped, last first:
A → B → C → E → D → F. A working read path plus an honest doc beats an API over empty
fetches. Password login (C) is required by the brief's definition of "working bridge";
the API (D) is the "via your app" part; F is portability.

### 0.4 Invariants — already decided, do not reopen
- One stable device/browser identity per user, never rotated (rotation = takeover signal).
  Persistent browser profile per user; `ttwid` minted once.
- Never blind-replay an ambiguous send. Never retry a challenge. Never auto-loop logins.
- Session at rest is envelope-encrypted (`session_store.py`); raw password never persisted
  by default, never logged.
- Detect at the right blast radius: all users failing at once = our bug (signer/browser/
  key); one user failing = that account.
- Per-user residential proxy via `proxy.py`; no IP cycling, no captcha solving, no
  fingerprint randomisation.
- Epistemic markers `[Obs]/[Inf]/[Guess]` in every doc and in code comments that state a
  protocol assumption.
- Code style: minimal comments, English, no emoji, no boilerplate docstrings. Match the
  existing modules' voice. `unittest` unless the tree already switched.

### 0.5 Overnight one-shot mode (active when `browser-data/<name>/` already exists)

I am asleep. I have pre-supplied the human input so you can run through without gates:

```
browser-data/<name>/capture-<ts>.jsonl   raw logged-in capture (responses, ws frames,
                                         hydration blob, login-page DOM dumps, in-page
                                         signing probe) — gitignored, contains secrets
browser-data/<name>/storage_state.json   plaintext session — gitignored
browser-data/<name>/meta.json            browser fingerprint (UA, locale, tz, screen)
browser-data/<name>/profile/             the persistent Chrome profile, if on the laptop
docs/notes/capture-notes.md              what I did and what happened (challenge? send?)
```

How the gates change:
- **G0**: answered in `docs/notes/capture-notes.md` (deadline, account, machine). Do not wait.
- **G1**: skip; the capture exists. First job of Task A becomes: verify
  `git check-ignore browser-data/` succeeds, then build `scripts/redact-capture.py` and
  `scripts/decode-frontier.py` against the real capture and produce the fixtures.
  Read the `inpage_probe` records first: they settle whether in-page `fetch` gets
  re-signed (compare `requested` vs `actually_sent` and the two statuses). If the
  `resigned` probe failed, the "page is the signer" claim in §2 is `[Guess]` again —
  say so in DESIGN.md and make `PageClient.call` fall back to replaying the page's
  own navigation flow (clicking/scrolling) instead of raw fetches.
- **G2**: self-approve with the checklist (five required fixture kinds present), record
  the result in `docs/DECISIONS.md`, continue. Missing kinds are documented gaps, not
  reasons to stop.
- **G3**: DO NOT run an automated password login against real TikTok overnight — a
  challenge nobody can solve may also burn the good session. Test the ladder against
  the fake platform only. Pin selectors from the `dom` records in the capture. Leave
  the real attempt as a morning gate in `FINISH-HERE.md`.
- **G4**: run the real sync yourself with the captured session: import
  `storage_state.json` + `meta.json` through the cookies flow (Task C), then `connect`
  → list conversations → backfill → subscribe. On the laptop, prefer the persistent
  `profile/` directory (same identity that logged in); on the server, import the
  storage state with the UA/locale/tz from `meta.json` pinned. Keep the socket open the
  rest of the night: I asked a friend to send a DM or two during the night, and every
  frame that arrives is a live realtime fixture. Log delivery lag for each.
  Read-only on the real account: no sends, no mark-read, no profile edits, unless
  `docs/notes/capture-notes.md` explicitly says `allow-send: yes`. Poll gently
  (≥ 30 s between REST calls when idle). If the session dies (`needs_user`,
  `status_code: 8`, redirect to `/login`): stop that login, do not re-login, record it,
  keep working from fixtures.
- After import, shred `storage_state.json` (`shred -u` or overwrite + delete) and note
  it in DECISIONS. The encrypted `session_store` blob is the only copy from then on.
- **G5**: write the final report to `FINISH-HERE.md` with a "Morning checklist" for me:
  the real password-login attempt (G3), a second live DM if none arrived, re-running
  the demo transcript with me watching, and the submission commit to push.

If something blocks you with no honest way around it (session refused on import, no
DM fixture decodable, chromium missing), do not stall the night on it: log it, pick the
next task in the priority order of §0.3, and leave the blocker at the top of
`FINISH-HERE.md`.

### 0.6 Unattended-run mechanics (always on in overnight mode)

- This brief is `docs/BRIEF.md`. `CLAUDE.md` must point to it. Keep `docs/PROGRESS.md`
  current: one line per finished step, the current task, the next action. **After any
  context compaction, re-read `docs/BRIEF.md` §0 and `docs/PROGRESS.md` before doing
  anything else.**
- Do not end your turn because you believe you are done. Before stopping, run the
  definition-of-done checklist in §9; if any item is open and the deadline in
  `capture-notes.md` allows, continue with the next unfinished item. Stop only when the
  checklist is complete or the deadline is within 3 hours (then finish docs and the
  morning checklist).
- You run with permission prompts disabled. In exchange, hard rules: never `git push`,
  never `git reset --hard`/`checkout --` on uncommitted work, never touch files outside
  this repo and `browser-data/`, never delete or modify `browser-data/<name>/profile/`,
  never run `shred`/`rm` on anything except the plaintext `storage_state.json` after a
  verified import, never install system packages, never change the laptop's power or
  network settings.
- Work on `integration` directly with one commit per step (no task branches in
  unattended mode — nothing to merge, nothing to conflict). Tag `case-study-submission`
  only in the morning checklist, not automatically.
- Use the same browser binary the capture used: the `launch` record in the capture
  says `channel: chrome` or `chromium`; the profile is only valid for that one.
- `.venv/` already exists with Playwright installed. Extend it (`.venv/bin/python -m pip
  install …`), do not recreate it, and do not let `setup.sh` recreate it either. Run
  tests with `.venv/bin/python -m unittest discover -s tests`.
- Docs are incremental, not a final task: when a task finishes, update its section in
  `DESIGN.md` and `README.md` before starting the next one. Task E is the final distill,
  not the first time the docs are written.
- If you hit the same error three times, stop retrying: log it in `docs/DECISIONS.md`,
  put it at the top of `FINISH-HERE.md`, move to the next item in the priority order.

---

## 1. Task 0 — orient (≤ 30 min), then gate G0

1. Read in order: `CLAUDE.md`, `FINISH-HERE.md`, `DESIGN.md`, `DESIGN-BROWSER.md`,
   `docs/observations/tiktok-web-2026-09-17.md`, the build log, the TikAPI evaluation, and
   the notes under `docs/notes/` (brainstorming, learning-from-beeper, webconsole findings,
   vaultbrowser build log). If any note is missing, list it at G0 — I will drop it in.
2. `git fetch --all`; merge the provider seam from `worktree-tikapi-eval` (`bridge/provider.py`,
   `bridge/providers/tikapi.py`, `bridge/auth/tikapi_oauth.py`, tests) into `integration`.
   Everything below is a second `MessageProvider` next to `im.IM` and `TikApiProvider`.
3. Inventory: modules, test count, what `run-web-login.sh` does today, drift between the
   build log and the tree. Reconcile the log if stale. Create `docs/DECISIONS.md`.
4. Gate G0 questions to me: which machine are you on; hard deadline; which TikTok account
   to use (must be a throwaway or one I accept getting challenged/locked; phone-verified);
   is a second account or a friend available to send a test DM; does `$DISPLAY` work on the
   laptop (paste output of `echo $DISPLAY && xeyes` or equivalent).

---

## 2. Path decision for this build: the web path ships, the mobile path is documented

Write into DESIGN.md §"Path selection", with markers:
- `[Obs]` The mobile path is gated from a datacenter by IP (`error_code 7`), device
  registration (TTEncrypt unavailable) and a signer we do not own. Correct, not runnable.
- `[Obs]` A real Chromium loads tiktok.com from the same IP, sets cookies, is not blocked,
  and runs TikTok's own web signer (`webmssdk` / `window.byted_acrawler`), which installs by
  monkey-patching `fetch` and `XMLHttpRequest.open`.
- `[Inf]` Any request executed inside the page (`page.evaluate(() => fetch(...))`) is
  therefore signed (`X-Bogus`/`X-Gnarly`/`msToken`) by TikTok's own code. The page is the
  signer; we never reverse the web signing algorithm.
- `[Obs]` The `/messages` page opens `wss://im-ws.tiktok.com/ws/v2` itself and handles
  acks/heartbeats. Playwright's native `page.on("websocket")` exposes every frame without
  injecting anything into the page. We do not drive the socket ourselves in this MVP.
- Cost, stated honestly: one browser context per connected user while syncing. Escalation
  path (documented, not built): a shared "signing oracle" browser + plain HTTP polling with
  `curl_cffi`; driving the frontier socket directly (pbbp2 framing already decoded).

`im.IM` (mobile) and `TikApiProvider` stay untouched behind `MessageProvider`. Add
`bridge/providers/web.py` (`WebProvider`). `Syncer`, `SyncState`, `normalize`, `metrics`,
`session_store` stay unchanged — they are "the pipeline".

---

## 3. Task A — one real logged-in capture (build the tool, then gates G1 + G2)

Nothing downstream can be built honestly without one authenticated capture. Build the
tool first, test it against the fake platform (Task C's `tests/fake_platform.py` — build
that stub now, minimal login + inbox pages), then raise G1.

`bridge/cmd/capture.py` + `run-web-capture.sh`:
- `launch_persistent_context(user_data_dir=browser-data/<user>/profile, headless=<cfg>,
  channel="chrome" if available else chromium)` through the existing stealth factory in
  `bridge/auth/browser.py`. Fixed fingerprint per user from the profile.
- Taps, none of them injected into the page:
  - `page.on("websocket")` → for any `im-ws.tiktok.com` socket record
    `{url, direction, ts, base64(frame)}` from `framereceived` / `framesent`;
  - `page.on("response")` → for URLs matching `im-api.tiktok.com`, `/api/im/`,
    `/passport/web/`, `/api/user/`, `/api/friend/`, `/api/relation/`, `/api/dm/`,
    `/api/inbox/` record `{url, method, request headers (redacted), request body, status,
    response headers, response body}`; also record every request URL host+path once, so we
    can see DM traffic we did not predict.
  - a CDP `Network.enable` session as a second WS tap (Chromium only), in case Playwright's
    event misses frames on a reused socket.
- Flow, mode `manual` (G1) / `auto` (Task C wires it):
  1. open `https://www.tiktok.com/login/phone-or-email/email`; in manual mode print
     "log in now" and wait for the `sessionid` cookie + `/messages` loading without a
     redirect to `/login` (timeout 10 min);
  2. record the `SIGI_STATE` / `__UNIVERSAL_DATA_FOR_REHYDRATION__` blob of `/messages`;
  3. print `NOW: send a DM to this account from the second account` and wait up to 120 s
     for a frontier frame that grows the inbox (this is the live inbound capture);
  4. click the first three conversations (selectors: prefer `data-e2e` attributes; record
     the selectors actually used in the observation doc), scroll each history up once to
     trigger pagination;
  5. open `/@<own handle>` and one contact's profile (avatar + profile JSON);
  6. in the last conversation, if a send box is present, do NOT send unless
     `--allow-send` is passed; with it, send the text `bridge test <ts>` once and record
     the request (needed for `send_text` in Task B).
- Persist: `browser-data/<user>/capture-<ts>.jsonl` (raw, gitignored) and the session into
  `session_store` (encrypted). `storage_state.json` must not remain on disk in plaintext.
- `scripts/redact-capture.py`: strips cookies, `msToken`, `sessionid*`, `sid_*`, `ttwid`,
  `uid_tt`, `verifyFp`, phone/email, replaces user ids and handles with stable fakes, keeps
  message text unless `--drop-text`. Output goes to `tests/fixtures/web/` with a
  `manifest.json`: one entry per fixture `{kind, url_pattern, method, captured_at,
  redacted: true, notes}`; `kind` ∈ `conv_list | messages_page | messages_more |
  ws_inbound_dm | ws_outbound | contacts | profile_self | profile_other | send_text |
  login_success | login_challenge | session_check`.
- `scripts/decode-frontier.py`: decodes captured pbbp2 frames with `bridge/proto.py`
  (field 6 = "gzip" → gunzip field 8), prints the field tree per frame, flags UTF-8 text
  runs, and emits a `docs/observations/frontier-fields.md` skeleton to fill.

Gate G1 (laptop, headful): I run `./setup-local.sh && ./run-web-capture.sh --mode manual
--headful --user <name>`, log in myself, clear any challenge, trigger the DM. Tell me in
the gate block to have the second account ready before starting, and that if TikTok pushes
me to verification or QR that is fine — note it, the capture only needs the session.

Gate G2: after redaction you produce `docs/observations/tiktok-web-dm-<date>.md` — every
DM-relevant URL, params, response shape, WS frame shapes, `[Obs]` marked — and list which of
the required fixture kinds are present. Required minimum: `conv_list`, one `messages_*`
with pagination, `ws_inbound_dm` decoded to readable text, `contacts`, `profile_other` with
avatar URL. I approve or re-run before Task B starts.

`[parallel-ok]` while waiting at G1/G2: Task C's fake platform and ladder tests, Task D's
API skeleton over the fake platform, Task E's README structure.

---

## 4. Task B — WebProvider: captured shapes → the existing pipeline (≤ 4 h)

Fixture-driven; every parser is tested against `tests/fixtures/web/`.

`bridge/web/session.py` — `WebSession`: storage_state ↔ `session_store` blob (cookies incl.
`ttwid`, `sessionid`, `sid_tt`, `uid_tt`, `msToken` snapshot, localStorage, fingerprint id
incl. UA/locale/timezone/viewport, proxy id, region from `SIGI_STATE`). `is_alive()` = the
cheapest in-page call the capture shows as a logged-in check (`[Obs]` candidate:
`/passport/web/account/info/`), fallback: load `/messages` and detect the
`/login?redirect_url=` 302 or `status_code: 8 "Login expired"`.

`bridge/web/page.py` — `PageClient`: one persistent context + page per login.
`call(method, url, params, body)` runs `fetch` inside the page, returns parsed JSON, maps
HTTP/body codes to `errors.py` (auth / rate-limited / banned / transient / invalid /
schema_change). Markers come from the capture, not guesses. `on_frame(cb)` subscribes to
Playwright websocket events. Reconnect = reload `/messages`; reconciliation is the
provider's job.

`bridge/web/frontier.py` — decode inbound frames with `proto.py`: outer `Frame` (field 5
header map incl. `X-Method`, `x_frontier_msg_id`; field 6 encoding; field 8 payload, gunzip
when "gzip"), inner body → `(conversation_id, message_id, ts, sender_id, text|None)` using
the field numbers observed in Task A. Unknown payload methods are logged at debug and
dropped, never raised. `x_frontier_msg_id` + message id = dedup key.

`bridge/providers/web.py` — `WebProvider(MessageProvider)`: `list_conversations(cursor)`,
`get_messages(conv_id, cursor)`, `list_contacts()`, `get_profile(user_id)` (avatar URL,
handle, nickname, `sec_uid`), `send_text` only if `send_text` fixture exists, else raise
`NotSupported` with a clear message; `mark_read` same rule. `subscribe()` yields normalized
events; on any gap (reconnect, reload, `x_frontier_msg_id` discontinuity) trigger a REST
reconcile of the affected conversations through the existing `Syncer` dedup path. Avatars
download through a separate jar-less, host-allowlisted client (`*.tiktokcdn.com`,
`*.tiktokcdn-eu.com`), hashed by URL.

`bridge/normalize.py` — web → canonical next to the mobile mapping: thread → `Thread{id,
type, participants, last_ts}`, user → `User{id, handle, nickname, avatar}`, message →
`Event{id, thread_id, sender_id, ts, kind=text|image|video|share|system, content, raw_ref}`.
Stable numeric ids as keys (bridgev2 portal/ghost rule). Keep the bridgev2 mapping table in
DESIGN.md current: Thread→Portal, User→Ghost, Event→Message, `WebSession`→`UserLogin.metadata`.

Tests: fixture parsing per endpoint; frontier decode of the captured DM; `WebProvider`
through the real `Syncer` with dedup (mirror the TikAPI interchangeability test); one
reconnect-gap reconcile test; `schema_change` raised on a fixture with a renamed field.

---

## 5. Task C — username + password login as a real flow, plus cookies import (≤ 3 h)

Port the vaultbrowser ladder into Python here (the state machine is small; a Node sidecar
buys nothing for the case study).

`bridge/auth/web_password_login.py`:
- Ladder: restore session → `is_alive()` → alive: done; else exactly one password login on
  TikTok's own login page (type into the real form) → success: persist session; challenge
  (captcha / 2FA / verify email / IDV): stop with `needs_user`, persist nothing new; wrong
  credentials: `bad_credentials`, refuse further automatic attempts until re-enrolment;
  locked: `blocked`.
- Selectors pinned from the Task A capture in one dict at the top of the file with the date
  verified; a missing selector is `schema_change`, not a crash.
- Password: default `store_password: false` — in memory for the single login, then dropped.
  Optional `store_password: true` uses `session_store` envelope encryption with a separate
  per-user data key; DESIGN.md's security paragraph states the trade-off (custody shrinks
  the exposure window, it does not close it; session-only is strictly better where it works).
- Register as `mode="password"` in `bridge/auth/login.py` next to `qr` and `email`.

`bridge/auth/web_cookie_import.py` — `mode="cookies"`: accepts `{cookies, user_agent,
local_storage, device: {ttwid, device_id, region}}`, builds a `WebSession`, pins the backend
context's UA/locale/timezone/viewport to what was reported, reuses the reported `ttwid`;
a UA mismatch with an existing profile is refused with `fingerprint_mismatch`, never
silently replaced. This is the laptop → server transfer (Task D) and the future iPhone
path (Task F). `scripts/export-session.py --user <u> --to https://<server>/v1/...` posts the
laptop session through this flow.

bridgev2 mapping in DESIGN.md: password = `user_input`, qr = `display_and_wait`,
cookies = `cookies`.

Tests: `tests/fake_platform.py` (Python port of vaultbrowser's fake platform: login,
wrong-password, 2FA mode, lock mode, inbox, send box) driven by a real headless browser:
session reuse across restart with zero password logins; logged-out-elsewhere recovers with
exactly one login; 2FA stops the ladder; wrong password never retried; cookies import →
alive; secrets never in logs (grep test).

Gate G3 (laptop): run `mode=password` once against real TikTok with the throwaway account.
Expected outcomes are all valid data: success, challenge (`needs_user`), or verification.
Tell me to send you the state + last 30 log lines. Record the outcome in the failure-mode
table ("password login from a fresh fingerprint: <what happened>").

---

## 6. Task D — "anyone can login via your app": API, pipeline, live run (≤ 3 h)

`bridge/api.py` (stdlib `http.server`, or FastAPI only if already in the venv):
- `POST /v1/login/start {flow: password|qr|cookies}` → `{login_id, step}`;
  `POST /v1/login/{login_id}/step {…}` → next step or `complete{user_login_id}`;
  `GET /v1/logins/{id}/status` → `connected | connecting | needs_user | bad_credentials |
  rate_limited | blocked | transient | unknown_error` + last error, last sync ts, and for
  `needs_user` an `action` (Task F); `DELETE /v1/logins/{id}` → remote logout if possible,
  wipe session and password; `GET /v1/logins/{id}/contacts`, `/threads`,
  `/threads/{id}/messages?cursor=`; `GET /metrics` (Prometheus text).
- Auth: `Authorization: Bearer <shared_secret>` from env, backend-only. Document the 1:1
  mapping to `/_matrix/provision/v3/login/*`.

`bridge/pipeline.py` — the "raw layer everything else reads from": SQLite tables `logins`,
`users`, `threads`, `events` with `stream_order` (ms ts), unique `(thread_id, message_id)`,
`ingested_at`, `source` (web|mobile|tikapi). Idempotent upsert. Optional webhook `POST` per
event to `pipeline.webhook_url` with retry + dead-letter file. `Syncer` writes here, the API
reads here.

`bridge/app.py` — one `BridgeApp` per process managing N logins: load every stored session,
connect each (validate → list conversations → resync → subscribe), push states to
`metrics`. A dead session surfaces `needs_user` and stops; it never spins.

Master key: `BRIDGE_MASTER_KEY` from env (KMS seam already exists). For the laptop → server
transfer the session is re-encrypted on import; the laptop and server keys need not match.

`scripts/demo.sh`: start API → login via `curl` (cookies flow against the imported session,
password flow against the fake platform in CI) → list threads → dump the last 5 events from
SQLite → show `/metrics`. Save a real run as `docs/demo-transcript.md` (redacted) — graders
cannot log in to TikTok, the transcript is the evidence.

Gate G4 (server): I export the laptop session with `scripts/export-session.py`, you run the
demo end to end on the server, send me a DM from the second account, and we confirm it lands
in SQLite with a delivery lag. Note the server's egress region vs the session's
`vregion`; a mismatch here is the "region mismatch" failure mode, observed live.

---

## 7. Task E — failure modes, metric, docs (≤ 3 h; this is what gets graded)

Failure-mode table in README and DESIGN.md, one row each, every row naming the test or the
live observation that backs it, the state it maps to, and the recovery:
password wrong · challenge (captcha/2FA/IDV) · session expired / logged out elsewhere ·
rate limited · account locked/banned · region mismatch (TTP cluster vs egress) · frontier
socket drop / gap · page reload loses signer state · TikTok DOM/schema change (selectors,
JSON shape) · signer/browser stale for all users (canary: cross-user 4xx spike) · proxy
dead · double login race · avatar CDN URL expired · ambiguous send · fingerprint mismatch
on import.

Metrics (`bridge/metrics.py`, at `/metrics`): headline `bridge_live_session_ratio` =
connected / total; leading indicator `bridge_password_login_share` (share of connects that
needed a password login — rising = sessions dying early, precedes challenges and bans);
`bridge_delivery_lag_seconds` p95 (TikTok ts → `ingested_at`); `bridge_error_total{state}`.

Docs:
- `DESIGN.md` distilled to 2–3 pages: architecture (6 layers + provider seam), path
  selection (§2), login/session flow (password ladder, cookies import, QR), how messages
  land in the pipeline, failure modes first, one concrete security paragraph (envelope
  encryption, password custody trade-off, third-party PII, retention and deletion on
  logout, secrets via env/KMS seam, log redaction, jar-less media client), the one metric,
  ToS/ban/detection risks with the escalation ladder (per-user proxy → signing-oracle
  browser → edge egress), bridgev2 mapping, iPhone client path (Task F), an explicit
  "verified live / verified against fixtures / fake-platform only / not built" list, and a
  **"Monday plan"**: the first five tickets a Knows engineer would pick up, in order, with
  the Go-port ticket last.
- README failure-modes-first, quickstart, "how to reproduce the capture" (G1 steps), demo
  transcript link.
- Update the build log with this phase, dead-ends included; update `CLAUDE.md` with the
  gate protocol and the current state so any later session starts here.

---

## 8. Task F — keep it usable from an iPhone app (≤ 2 h)

The bridge stays on the backend; an iOS app is a client of the Task D API, the same way
Knows' apps are clients of its mautrix fork. iOS cannot run a long-lived background sync
(Background App Refresh is throttled, no persistent sockets), so on-device ingest is not a
goal. The cookies flow from Task C is already the phone's login path; what remains:

1. **Injected JS as standalone assets** for the future `WKWebView` host: `bridge/web/inject/
   ws_hook.js`, `fetch_tap.js`, `request.js`, each with one entry point posting through a
   single `postToHost(kind, payload)`. Server binding: `expose_binding("__bridge_host", …)`
   (used only where in-page execution is needed; the server keeps Playwright's native events
   for capture). iOS binding, documented not built: `WKUserScript` at `.atDocumentStart` +
   `WKScriptMessageHandler` — a foreground "live while the app is open" mode for free later.
   Test: each file installs in a headless page without touching `navigator` properties.
2. **`needs_user` carries an action the app can render:** `{state: "needs_user", reason:
   captcha|2fa|verify_email|idv|selector_missing, action: {open_url:
   "https://www.tiktok.com/login", then: "cookies"}}`. A failed password login degrades into
   the cookies flow. Document the app-side sequence in DESIGN.md: password relay → on
   `needs_user` open the URL in a `WKWebView` → user solves it on TikTok's page → app reads
   `WKHTTPCookieStore` → `cookies` step → connected.
3. **What does not transfer:** state in DESIGN.md that the mobile-app-API path (SignerPy,
   `device_register`, TTEncrypt) hits the same wall on a phone as on a server, and that
   "run the bridge on the device" is rejected for iOS background limits; edge egress stays
   a documented escalation lever, not a build target.

---

## 9. Rules for the whole session, and gate G5

- Never claim TikTok behaviour you did not observe in a capture; mark it `[Guess]` and add
  it to the "to verify" list.
- Never add captcha solving, IP rotation, or fingerprint randomisation.
- Before each commit: `python -m unittest discover -s tests` green; no secrets in the diff
  (`git diff | grep -iE 'sessionid|msToken|ttwid|verifyFp|password='` empty outside
  redacted fixtures); `docs/DECISIONS.md` updated.
- Supervised mode: branch per task, merged into `integration`, tag
  `case-study-submission` at the end. Unattended mode: see §0.6.
- Gate G5: final report to me — what is proven live, proven against fixtures, fake-platform
  only, not built; the open `[Guess]` list; the DECISIONS log; and the exact commit to
  submit.

### Definition of done (run this before you consider stopping)
1. `.venv/bin/python -m unittest discover -s tests` green; count recorded in PROGRESS.md.
2. `tests/fixtures/web/manifest.json` exists; every present fixture kind parses through
   `WebProvider`; the five required kinds are listed as present or documented as missing.
3. `docs/observations/tiktok-web-dm-<date>.md` written from the real capture, with the
   in-page signing probe result stated as `[Obs]`.
4. Password ladder + cookies import pass against the fake platform; no real-account
   password attempt was made.
5. Real-account sync attempted with the captured session; outcome (connected / needs_user
   / fingerprint_mismatch) recorded with timestamps in DECISIONS.md; plaintext
   `storage_state.json` shredded after import.
6. API + pipeline run against the fake platform; `docs/demo-transcript.md` exists
   (fake-platform run at minimum, real-session run if the session was alive).
7. `DESIGN.md` ≤ 3 pages with every section from Task E incl. the Monday plan and the
   verified/fixtures/fake-only/not-built list; `README.md` failure-modes-first.
8. `FINISH-HERE.md` has the morning checklist; `docs/DECISIONS.md` and `docs/PROGRESS.md`
   are current; `CLAUDE.md` points to the brief.
9. `git log -p integration | grep -iE 'sessionid=|msToken=|ttwid=|verifyFp=' ` is empty
   outside redacted fixtures, and `git status` shows nothing from `browser-data/`.