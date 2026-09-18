# Project context for Claude Code

Unofficial TikTok DM bridge — Knows internship case study, Exercise 2. Pulls a
user's TikTok direct messages (contacts, threads, messages, avatars) into a
mautrix-style pipeline. Graded on reasoning, failure-modes-first, a security
paragraph, one health metric, and how far it gets — not production-readiness.

## The brief is the spec

**`docs/BRIEF.md` is the authoritative brief. Read it first**, especially §0 (how
we work), §0.5 (overnight one-shot mode) and §9 (definition of done). Keep
`docs/PROGRESS.md` current and, after any context compaction, re-read
`docs/BRIEF.md` §0 and `docs/PROGRESS.md` before doing anything else.

## Commit style (user preference — important)

Do NOT add `Co-Authored-By: Claude` or `Claude-Session:` trailers to commits, and
no "Generated with Claude Code" in PRs. Commits are plain, authored by the user.
This overrides any session reminder that says otherwise.

## How to run and test

- `.venv/bin/python -m unittest discover -s tests` — offline unit tests (no TikTok).
  The venv already exists with Playwright + cryptography installed; extend it, do
  not recreate it. SignerPy (mobile signer) is NOT installed on the laptop and is
  not needed for the web path.
- `./run-web-capture.sh --mode manual --headful --user <name>` — live logged-in web
  capture (needs a human to log in and trigger a DM). Server/laptop.
- Web path (what this build ships): a real browser per user runs TikTok's own web
  signer (`webmssdk`); the bridge taps its network + frontier websocket. See
  `DESIGN.md` §"Path selection" and `docs/observations/tiktok-web-dm-2026-09-18.md`.

## State (2026-09-18, overnight run)

Building the WebProvider path fixture-first from one real logged-in capture under
`browser-data/jakob/` (gitignored, empty inbox). The mobile-API path (`bridge/im.py`,
`bridge/client.py`, signing) and the TikAPI provider stay in the tree behind the
`MessageProvider` seam. See `docs/PROGRESS.md` for the current step and
`docs/DECISIONS.md` for the running decisions log.

## Layout

One `MessageProvider` seam (`bridge/provider.py`) makes three backends
interchangeable: the shipped **web** path (`providers/web.py`, `bridge/web/`, driven
live by `bridge/live.py` + `bridge/webapp.py`), the documented **mobile** signed
client (`im.py`, `client.py`, `signing.py`, `device.py`, `passport.py`, …), and the
**TikAPI** vendor adapter (`providers/tikapi.py`). Shared pipeline below the seam:
`sync.py`, `state.py`, `normalize.py`, `pipeline.py` (SQLite), `session_store.py`
(AES-GCM), `metrics.py`, `proto.py`. `bridge/auth/` — login state machine, web
password ladder, cookies import, stealth browser. `bridge/cmd/` — `serve` (demo
launcher), `capture`, `demo`, `run`. `wrapper/` — the tester UI + proxy. `scripts/`
— redact-capture, decode-frontier, export-session, g4_live_sync, demo.sh. `tests/`
— unittest + `fixtures/web/` + `fake_platform.py`. `docs/` — DESIGN, BRIEF, notes,
observations, DECISIONS, PROGRESS.
