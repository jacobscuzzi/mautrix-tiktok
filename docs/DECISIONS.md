# Decisions log

Small, non-blocking calls made during the unattended run. Format:
`date · decision · why · how to reverse`. Gate-relevant results are summarized at
each gate. Newest at the bottom.

## 2026-09-18 (overnight one-shot)

- 2026-09-18 · Treat `docs/BRIEF.md` as authoritative and restore it + `docs/notes/`
  from branch `chore/packaging` into `integration` · the brief and the pre-existing
  research notes were committed on `chore/packaging`, not `integration`, but the run
  operates on `integration` · `git rm` them; they are additive docs.
- 2026-09-18 · Restore the gitignored `browser-data/jakob/` capture (captures,
  storage_state, meta, profile) into the working tree from `chore/packaging` via
  `git archive`, without staging · `.gitignore` excludes `browser-data/`, so the
  files are present in the commit but absent from a fresh `integration` checkout;
  Task A needs them · they stay gitignored; nothing to reverse.
- 2026-09-18 · Merge only the provider-seam files from `worktree-tikapi-eval`
  (`bridge/provider.py`, `bridge/providers/tikapi.py`, `bridge/auth/tikapi_oauth.py`,
  `tests/test_provider.py`, `tests/test_tikapi.py`) by `git checkout`, not a branch
  merge · that branch forked before later `integration` work and a full merge would
  revert modules · delete the files; they are additive (+29 tests).
- 2026-09-18 · Honor the user preference from the prior-art `CLAUDE.md`: plain
  commits, no `Co-Authored-By`/"Generated with Claude Code" trailers · it is an
  explicit user instruction about this repo (Git author "Jakob Scuzzi") and the
  session reminder yields to the user's own instruction · re-add trailers if asked.
- 2026-09-18 · Make `tests/test_signer_integration.py` skip when SignerPy is not
  importable (not just when the shim file exists) · SignerPy is installed on the
  server but not on this laptop, and the mobile signer is irrelevant to the web
  path being built · revert the skip guard.
- 2026-09-18 · The account inbox was empty at capture time ("No messages yet"), so
  `conv_list` is an empty-but-valid init response and there is no `messages_*`,
  `ws_inbound_dm`, or `send_text` fixture · these are documented gaps per §0.5, not
  reasons to stop; the parsers are still built and tested against synthetic
  fixtures shaped from the real envelopes · re-run the capture once a DM exists.

## G0 summary
Answered from `docs/notes/capture-notes.md`: laptop (WSL2, DISPLAY=:0, Playwright
chromium), throwaway account `Contact 34` (EU-TTP2), deadline "morning",
friend sends in the morning, allow-send: no. Proceed without waiting.

## G1/G2 summary (Task A)
Skipped G1 (capture exists). G2 self-approved: contacts + profile_other captured
live with real data; conv_list present but empty (empty inbox); messages_* and
ws_inbound_dm are documented gaps. Fixtures + manifest in tests/fixtures/web/,
observation in docs/observations/tiktok-web-dm-2026-09-18.md. Continued to Task B.

- 2026-09-18 · WebProvider parses JSON DM shapes; the live conversation-list is
  protobuf (im-api get_by_user_init) and was empty, so list_conversations/get_messages
  are tested against synthetic JSON fixtures shaped from the real envelopes · the
  parser and the pipeline wiring are what is graded; the exact live JSON mirror is
  a documented gap to confirm on a non-empty inbox · swap the URL/parser when a
  real conv list is captured.
- 2026-09-18 · WebProvider.send_text / mark_read raise NotSupported (no send
  fixture, allow-send: no) rather than blind-firing · never send an unverified
  request against a real account · capture with --allow-send, add the fixture,
  implement.

## G3 handling (Task C)
Per §0.5 no real-account password login overnight. The ladder is proven against
the fake platform only: 6 real-headless-browser e2e cases (first login, session
reuse across restart with zero logins, logged-out-elsewhere -> exactly one login,
wrong password -> bad_credentials, 2FA -> needs_user, locked -> blocked) plus the
pure state-machine unit tests. The real attempt is a morning gate in FINISH-HERE.md.

- 2026-09-18 · Quote attribute values in the pinned CSS selectors
  (`[data-e2e="2fa-input"]`) · Chromium rejects unquoted attribute values that
  start with a digit · none.

## G4 result — LIVE, read-only (2026-09-18 08:23–08:24 CEST)
Ran `scripts/g4_live_sync.py` against the real account with the captured persistent
profile (channel chromium, headless). **Outcome: connected.**
- Landed on `/messages` with no `/login` redirect; `is_alive()` via the signed
  in-page `/passport/token/beat/web/` call returned true. The session captured at
  06:55 was still alive ~90 min later.
- **19 contacts pulled live** from `/api/im/spotlight/relation/` through the real
  WebProvider (in-page signed fetch) into a scratch SQLite pipeline. This is the
  read path proven end to end against a live session, not fixtures.
- Frontier socket opened; 1 inbound frame (sync/cursor, no DM — inbox empty, no
  friend DM overnight as noted in capture-notes). Delivery lag: n/a (no message).
- Read-only honored: no sends, no mark-read (allow-send: no). Gentle: one pass +
  ~45 s socket hold, not a night-long browser hold (lower anomaly risk).
- After the verified import into `session_store` (encrypted blob
  `browser-data/jakob/<uid>.session`, loads back and matches), the plaintext
  `browser-data/jakob/storage_state.json` was **shredded** (`shred -u`). The
  encrypted blob is now the only at-rest copy; the persistent `profile/` (untouched)
  remains the working credential on the laptop. Dev master key persisted locally at
  `browser-data/jakob/master.key` as a KMS stand-in (prod: BRIDGE_MASTER_KEY/KMS).

- 2026-09-18 · Store the dev master key next to the encrypted blob under the
  gitignored browser-data/ · overnight has no KMS/BRIDGE_MASTER_KEY and the
  plaintext alternative is worse; encryption-at-rest against disk theft is weaker
  this way but honest · production reads the key from env/KMS (seam already exists).

## G4 follow-up — live DM captured, frontier parser corrected (2026-09-18)
A friend sent ~10 DMs during a second `g4_live_sync.py --watch` run. The frames
were saved (`browser-data/jakob/capture-g4-*.jsonl`, gitignored) and decoded: my
`[Inf]` frontier message layout was wrong. Real layout, now pinned:
`body f6 -> f500 -> f5 = Message{1 conversation_id, 3 server_message_id,
4 create_time(microseconds), 7 sender_id, 8 content JSON}`; read-receipts carry
`command_type` and are skipped. `frontier.py` rewritten to this; it extracts all 10
messages with correct text and **correct self-vs-peer sender attribution** (self-sent
messages carry `f7 = self uid`). `ws_inbound_dm` is now verified live.

- 2026-09-18 · The committed `ws_inbound_dm.json` fixture uses the REAL frame layout
  but NEUTRAL text + pseudonymized ids · the real messages are a friend's private
  DMs (third-party PII); they stay only in the gitignored capture · a skipUnless test
  (`test_real_captured_dm_frames_if_present`) proves the parser on the real frames
  locally without committing them.

## Demo app — the runnable wrapper (2026-09-18)
Built a one-command demo per request: `./bridge-app.sh` starts the bridge
(`bridge/live.py` LiveBridge: a single Playwright worker thread that opens a real
login browser per user, detects login, encrypts the session, syncs contacts/threads/
messages, streams DMs over the frontier socket) + `bridge/webapp.py` (JSON API +
/metrics) + the `wrapper/` tester UI (connect / chats / health, proxying to the
bridge). Real-TikTok-login only (user's choice). Session envelope-encrypted at rest;
logout wipes session + pipeline rows + browser profile (`pipeline.wipe_login`). The
UI shows a data-handling explainer before login. Non-browser plumbing is unit-tested
(test_webapp); the real login is inherently manual (the demo).

## Demo UX: background session + media rendering (2026-09-18)
- After a successful login the visible browser window is closed and the SAME
  persistent profile is reopened HEADLESS in the background (`LiveBridge._go_background`),
  so the bridge keeps syncing with no window on screen. The session is saved before
  the swap; the profile lock releases immediately in practice (retry loop as a guard).
  Login uses the headful window; all ongoing work is headless.
- GIFs/images/stickers: `normalize._media_of` extracts any media URL from a message's
  content JSON generically (no GIF sample was in the capture), sets kind=gif/image/
  sticker with the URL as the body; the wrapper renders it as an <img> (GIFs animate).
  A message whose text merely contains a URL stays text. If TikTok wraps a GIF in an
  unexpected shape, the generic URL search still catches it; a bad URL falls back to
  a "[gif]" label via onerror.

## Demo UX: existing chats on connect + scroll (2026-09-18)
- Reported: fresh login showed no existing chats until a new DM arrived. Probed live:
  the frontier does NOT replay history on connect (0 msgs, 1 sync frame); the DOM has
  only nicknames/previews; the backlog is the page's own protobuf
  `get_by_user_init` (6.7 KB populated). Its messages are the SAME Message shape as
  frontier frames at `f6 -> f203 -> f1[]`, so the frontier parser is reused
  (`messages_from_init_body`). LiveBridge captures that response on `/messages` load
  and ingests it on connect. Verified live: 2 conversations x 5 messages appear
  within ~6 s of connect with no new message sent.
- Peer names/avatars for non-followed contacts via `GET /tiktok/v1/im/user/profile/
  ?user_ids=[...]` (`WebProvider.get_profiles`; the old `uid` param was wrong).
- Chat scroll: newest stays at the bottom; re-render only when messages changed; if
  the user scrolled up, their position is kept (no yank on the 5 s poll).
- A sticker whose content is `{}` is labeled `[sticker]` instead of an empty bubble.

## Load older messages (2026-09-18)
Added on-demand history pagination. Probed live: scrolling up does not auto-fire a
call once the backlog is loaded; the page uses `POST .../v1/message/get_by_conversation`
(protobuf) with a microsecond cursor. Rather than depend on flaky programmatic-scroll
triggering, the bridge replays that call directly through the in-page signer:
`proto.encode_tree` builds the request from the captured get_by_user_init template
(swap f1=301, f8=command{conv, short_id, cursor, count}); `page.playwright_pb_poster`
POSTs raw protobuf so webmssdk signs it; the response decodes with
`frontier.messages_from_conversation_body` + `conversation_cursor` (f2 next cursor,
f3 has_more). `LiveBridge.load_older` (via a worker `submit`) ingests + dedups and
advances the per-conversation cursor. API `POST /api/load_older`; the wrapper adds a
"Load older" button that keeps scroll position. Verified live: 5 -> 21 messages in one
call, has_more=0. conversation_short_id comes from message field 5.

## Login throttle fix: one stable profile, reuse the session (2026-09-18)
Reported: "Maximum number of attempts reached" on the login. Cause was ours: connect()
made a NEW random browser profile each time, so every test was a fresh device identity
doing a fresh login -- exactly what TikTok rate-limits, and a violation of the design's
"one stable identity, never rotated" rule. Fix: connect() reuses ONE stable profile
(`<data_dir>/session`); _do_open first opens it HEADLESS and, if a live session exists,
goes straight to connected with no login window and no new login. A visible login
window opens only when there is genuinely no session. Verified live: reconnecting to an
existing session reaches connected with headful_login=False (no window). Also: the
login page's "maximum number of attempts / try again later" is detected and surfaced as
a clear needs_user message. Note: the TikTok cooldown that is already tripped must
expire on its own (~15-60 min); the fix prevents it recurring. "Log out & wipe" still
deletes the profile (forcing a fresh login next time), so avoid it mid-testing.

## QR login as an alternative (2026-09-18)
Added a passwordless QR option for users who don't know their password/email. QR is
the design's primary/safest path (phone approves, no credentials, no captcha, no
password throttle), and login detection is method-agnostic (it waits for the sessionid
cookie), so it was nearly free: connect(flow="qr") opens the login window at
/login/qrcode instead of the email form; the user scans with the TikTok app and the
session is captured the same way. flow is threaded through connect() and the API;
password_login_used is False for QR (more accurate leading indicator). UI: two buttons
("Scan a QR code" / "Use password / email"). Verified live: the QR page opens with a
real QR rendered.

## Send messages (2026-09-18)
Added outbound text by driving TikTok's OWN composer (the page signs the send), not
by crafting a signed send request -- the most human-like and lowest-fragility path.
Composer probed live: a Draft.js `[contenteditable][role=textbox]` ("Send a message…"),
no send button -> Enter sends. Flow (LiveBridge._send, worker submit): open the target
conversation (click items, verify via the get_by_conversation conv id), focus the
editor, type, press Enter. Honors the invariants: ONE send at a time (login.sending
guard), and NEVER blind-replays -- one send, confirmed by our own message returning
over the frontier (already ingested/rendered as a "me" bubble); an ambiguous result is
not retried. API POST /api/send; UI adds a send box per conversation. Verified live up
to the send WITHOUT sending (typed a draft into the editor, read it back, cleared it);
the first real send is left to the user (outward-facing: it DMs a real contact). Risk
noted: automated sending is a stronger bot/ban signal than reading, so keep it gentle
and per the user's explicit request.

## Codebase cleanup for review (2026-09-18)
Dead-code/file audit (pyflakes + import/reference scan). Removed superseded files:
`bridge/auth/web_qr_login.py` and `web_dm_capture.py` (replaced by `bridge/cmd/capture.py`
+ `bridge/live.py`), `run-web-login.sh` (replaced by `bridge-app.sh` / `run-web-capture.sh`),
`DESIGN-BROWSER.md` (folded into DESIGN.md §1), and `beeper-extraction.md` (a duplicate of
`docs/notes/learning-from-beeper.md`). Removed dead imports (uuid, base64, unused ladder
constants, test locals). Kept the mobile-IM and TikAPI providers on purpose: they are the
documented build-vs-buy alternatives behind the MessageProvider seam and are unit-tested.
README/CLAUDE layout rewritten to state clearly what ships (web) vs the documented
alternatives. pyflakes clean; 198 tests still green.
