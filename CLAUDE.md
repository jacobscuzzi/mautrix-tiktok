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

`bridge/` — proto codec, envelope, errors, device, signing (mobile), client, session
store (AES-GCM), state, normalize, sync, im, passport, metrics, app, config, proxy,
provider seam (`provider.py`, `providers/tikapi.py`); `bridge/web/` — web session /
page client / frontier decode (Task B); `bridge/auth/` — login state machine, web
password login + cookies import (Task C); `bridge/cmd/` — CLIs incl. capture.
`scripts/` — redact-capture, decode-frontier, export-session, demo. `tests/` —
unittest + `tests/fixtures/web/`. `docs/` — BRIEF, DESIGN, notes, observations.
