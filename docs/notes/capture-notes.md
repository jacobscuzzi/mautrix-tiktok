# Capture notes — overnight one-shot run (Jakob, 2026-09-18)

This is the human-input file the brief (§0.5) reads instead of running the G0/G1
gates live. It records the machine, account, deadline and what happened during the
capture, so the unattended run can proceed without waiting.

## G0 answers (machine / account / deadline)

- **Machine:** this laptop — Windows + WSL2 Ubuntu, `$DISPLAY=:0` (WSLg present),
  sudo available, Python 3.12.3, Playwright 1.63 with bundled Chromium (channel
  `chromium`, not system `chrome`). This is NOT the datacenter server; there is no
  `.chromium-libs/` here and no residential-proxy egress.
- **Account:** throwaway TikTok web account `Contact 34`
  (uid `0000000000000000000`), region **DE / EU-TTP2**. Acceptable to get
  challenged or locked. Phone-verified.
- **Deadline:** not stated explicitly. Treated as "morning" (assume ≥ 8 h from the
  07:5x start). Definition-of-done in §9 is the stop condition; finish docs + the
  morning checklist if the clock runs short.
- **Second account / friend:** a friend will send a DM **in the morning**, not
  tonight. No inbound DM is expected overnight.

## What the capture is

Two logged-in web captures live under `browser-data/jakob/` (gitignored):

- `capture-1789707342.jsonl` — 06:55 CEST, login + `/messages` + hydration + DOM
  + frontier WS frames. 827 records.
- `capture-1789708291.jsonl` — 07:11 CEST, a second logged-in session that also
  ran the in-page signing probe (`inpage_probe` records) and dumped the final
  cookie jar. 582 records.
- `storage_state.json` — plaintext session jar (to be imported then shredded).
- `meta.json` — fingerprint: UA Chrome/153 on X11 Linux, en-US, Europe/Paris,
  1280×720, dpr 1, webdriver false.
- `profile/` — the persistent Chromium profile that logged in (do NOT modify/delete).
- `messages.png` — screenshot of the empty inbox.

The capture tool was `capture_now.py` (on branch `chore/packaging`), the standalone
ancestor of `bridge/cmd/capture.py`.

## What happened during the capture (the honest state)

- Login succeeded (QR flow on `/login/qrcode`, `login_success` after ~123 s;
  `sessionid` cookie present). No captcha/challenge tripped.
- **The inbox was empty** — the `/messages` DOM shows `dm-new-conversation-list`
  = "No messages yet". So there were **no conversations, no message history, and no
  inbound DM** to capture. The frontier socket opened and exchanged only
  sync/cursor frames (gzipped `PayloadRelatedMethod`), never a message payload.
- The self-sent "bridge test" DM mentioned for the overnight run is **not present**
  in either capture file (grep for `bridge test` → 0 hits). It was not sent, or was
  sent after the capture closed. Treat inbound/outbound message fixtures as gaps.
- The **in-page signing probe DID run** (capture 2). It fetched a DM URL inside the
  page twice: once with signing params stripped, once as captured. Both came back
  HTTP 200 with real JSON, and the network trace shows TikTok's own `webmssdk`
  re-appended `X-Bogus`/`X-Gnarly`/`X-Dynosaur`/`msToken`. **The page re-signs
  in-page fetches.** This confirms the "page is the signer" claim as [Obs]; the
  §2 fallback (replay navigation) is not needed.

## allow-send

**allow-send: no.** No sends, no mark-read, no profile edits against the real
account tonight. Read-only only (G4).

## Gate handling for this run

- G0: answered here. Do not wait.
- G1: skip — the capture exists. Build the redactor/decoder against it.
- G2: self-approve with the fixture checklist; missing message-bearing kinds are
  documented gaps (empty inbox), not stop conditions.
- G3: do NOT run a real password login overnight. Fake platform only. Leave the
  real attempt as a morning gate.
- G4: import the captured session read-only, attempt a live sync. If the session
  refuses on import or the browser cannot run here, log it and keep working from
  fixtures.
- G5: write the final report + morning checklist to `FINISH-HERE.md`.
