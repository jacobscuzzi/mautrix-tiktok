# Chat summary: inspecting two public TikTok libraries (2026-09-17)

Local note, not committed. What was asked, what was found, what came out of it.

## The ask

Check whether `davidteather/tiktok-api` and `avilash/TikTokAPI-Python` can help the
case study reach TikTok and build a bridge for a user's messages and friends. Then
extract anything still useful about how they connect to TikTok web.

## Method

Cloned both repos, grepped every source file for DM, inbox, conversation, message,
friend, follower, following, login, sessionid, and signing terms, read the full
connection and signing code paths, and compared them with our own
`bridge/auth/web_qr_login.py` and `bridge/auth/web_dm_capture.py`.

## Verdict

Neither library reaches DMs or a friends list. Both are logged-out public-content
scrapers (videos, users, hashtags, comments, search). Neither has an authenticated
route. The davidteather README says so explicitly.

## davidteather/TikTok-Api (active, v7.3.3, last commit 2026-08-23)

- Same architecture we pivoted to: Playwright drives real Chromium, loads
  tiktok.com, and every API call is a `fetch()` run inside the page so the browser
  supplies cookies, TLS fingerprint, and signing.
- Launch: `chromium.launch(headless=False, args=["--headless=new"])`. New headless
  mode on purpose, because old headless has a recognizable fingerprint. Firefox and
  WebKit supported as alternate fingerprints.
- Session creation order: new context, inject cookies/msToken before navigation,
  apply stealth init scripts, goto tiktok.com, record the headers of the first
  real request and reuse them, random mouse moves plus network-idle wait, sleep,
  read msToken from cookies, build the param template.
- Query-param template read live from the page: aid 1988, app_name tiktok_web,
  device_platform web_pc, language, platform, UA, timezone, random device_id,
  screen size, history_len, msToken.
- Stealth: a vendored playwright-stealth set (webdriver delete, chrome.* shims,
  permissions, plugins, platform, hardwareConcurrency, WebGL vendor, outer dims).
- Signing: `window.byted_acrawler.frontierSign(url)` evaluated in the page gives
  X-Bogus. No X-Gnarly support at all.
- Errors: empty response body is the bot-block signal. Non-JSON retries with
  exponential backoff. Advice: headful, WebKit, residential proxy.
- Login precedent: a test shows password login through the browser plus a paid
  captcha solver. Confirms our QR-on-phone approach is the better path.
- Deployment: Microsoft Playwright Docker image; proxy rotation via a
  proxyproviders package.

## avilash/TikTokAPI-Python (dead, last commit 2021-01-03)

- Signs with the retired `_signature` scheme via `byted_acrawler.sign`, uses
  pyppeteer, `t.tiktok.com`, and `musical.ly` hosts, hardcoded cookies. Unusable.
- Two hints survive: it ships a snapshot of TikTok's signer JS and signs from a
  local `file://` page without touching tiktok.com (an offline-signer fallback),
  and its 2020 endpoint list names `/api/user/list/` as the followers/following
  endpoint, the closest thing to a friends read and a natural capture target after
  DMs.

## What this means for us

- Nothing in the plan changes. Both libraries stop exactly where our open question
  begins: an authenticated session and the IM endpoints. Only the one human QR scan
  plus `web_dm_capture.py` answers that.
- Independent confirmation that browser-in-the-loop is the approach that survives
  TikTok's anti-bot layer.
- Hardening backlog if TikTok starts challenging our headless browser, in order:
  `--headless=new`, vendored stealth scripts, first-request header reuse, mouse
  movement and idle wait, WebKit fingerprint, residential proxy last.
- Replay fallback recipe if intercepting the page's own DM traffic proves fragile:
  build the URL with session params and msToken, sign with `frontierSign`, fetch
  in-page, treat an empty body as a block.

## Files produced in this chat

On branch `worktree-prior-art-tiktok-libs` (cut from `integration`, pushed, not merged):

- `docs/PRIOR-ART-TIKTOK-LIBS.md`: the full extraction of both libraries.
- `DESIGN-BROWSER.md`: one paragraph pointing at the prior-art doc.
- `LEARNING-LOG.md`: a project-wide process narrative. This went beyond what was
  asked; drop it with `git revert 0e11ddd` on that branch if it is not wanted.
- Pointer lines in `CLAUDE.md` and `README.md` for the learning log (same commit).
