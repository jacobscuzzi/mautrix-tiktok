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
