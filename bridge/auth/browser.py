"""Stealth browser factory for the web path.

One persistent Chromium context per user, launched from that user's
`browser-data/<user>/profile` directory so the device identity (ttwid, wid,
localStorage) is minted once and never rotated -- the invariant from DESIGN.md.
The fingerprint (UA, locale, timezone, viewport) is pinned from the user's
`meta.json` so a reused session never contradicts the identity that created it.

Nothing here is injected into the page. TikTok's own `webmssdk` does the signing;
we only tap the network and the frontier websocket through Playwright's native
events. `page.evaluate(fetch)` inside the page is therefore signed by TikTok's own
code (confirmed live: the in-page probe re-signs a stripped URL).
"""
from __future__ import annotations

import json
import os

# Only the automation tells we can remove without touching navigator properties
# (the observation notes: webmssdk cross-checks navigator, so do not overwrite it).
STEALTH_ARGS = ["--disable-blink-features=AutomationControlled"]
IGNORE_DEFAULT = ["--enable-automation"]

DEFAULT_META = {
    "ua": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36"),
    "lang": "en-US",
    "tz": "Europe/Paris",
    "iw": 1280,
    "ih": 720,
}


def load_meta(browser_dir):
    """Read the pinned fingerprint for a user, or the default if none saved yet."""
    path = os.path.join(browser_dir, "meta.json")
    meta = dict(DEFAULT_META)
    if os.path.exists(path):
        with open(path) as f:
            meta.update(json.load(f))
    return meta


def context_kwargs(meta):
    """New-context options that pin locale/timezone/viewport to the fingerprint."""
    return {
        "user_agent": meta.get("ua", DEFAULT_META["ua"]),
        "locale": meta.get("lang", DEFAULT_META["lang"]),
        "timezone_id": meta.get("tz", DEFAULT_META["tz"]),
        "viewport": {"width": int(meta.get("iw") or 1280),
                     "height": int(meta.get("ih") or 720)},
    }


def _launch_kwargs(profile_dir, headless, extra_args):
    return dict(
        user_data_dir=profile_dir,
        headless=headless,
        args=STEALTH_ARGS + list(extra_args or []),
        ignore_default_args=IGNORE_DEFAULT,
    )


def launch_persistent(playwright, browser_dir, *, headless=True, channel=None,
                      extra_args=None):
    """Launch the persistent context for a user.

    Prefers `channel="chrome"` (a real Chrome) when asked and available, else the
    Playwright-bundled Chromium -- the profile is only valid for the binary that
    created it, so the capture's `launch` record decides which one to reuse.
    Returns (context, channel_used).
    """
    profile_dir = os.path.join(browser_dir, "profile")
    os.makedirs(profile_dir, exist_ok=True)
    meta = load_meta(browser_dir)
    kw = _launch_kwargs(profile_dir, headless, extra_args)
    kw.update(context_kwargs(meta))
    # viewport=None lets a headful window own its size; pinning matters for headless.
    if not headless:
        kw["viewport"] = None
    if channel == "chrome":
        try:
            ctx = playwright.chromium.launch_persistent_context(channel="chrome", **kw)
            return ctx, "chrome"
        except Exception:
            pass
    ctx = playwright.chromium.launch_persistent_context(**kw)
    return ctx, "chromium"
