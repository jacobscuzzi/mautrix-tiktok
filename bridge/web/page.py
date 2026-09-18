"""PageClient: one persistent browser context + page per web login.

`call(method, url, params, body)` runs `fetch` INSIDE the page, so TikTok's own
webmssdk signs it (confirmed live: the in-page probe re-signs a stripped URL).
The result is parsed JSON, and HTTP / body codes map to the shared error taxonomy.
`on_frame(cb)` subscribes to the frontier websocket via Playwright's native events
(no injection). Reconnect = reload /messages; the provider reconciles the gap.

The Playwright dependency is isolated behind an injectable `evaluator` so the
whole class is unit-testable without a browser: pass a callable
`(method, url, params, body) -> (status, text)` that returns fixture bytes.
"""
from __future__ import annotations

import json
from urllib.parse import urlencode

from .. import errors

MESSAGES_URL = "https://www.tiktok.com/messages"

# TikTok body-level status codes seen / inferred. 0 = ok.
AUTH_CODES = {8}            # "Login expired" [Obs]
RATE_CODES = {7}           # passport "max attempts" [Obs]


class PageClient:
    def __init__(self, evaluator, on_reload=None):
        self._eval = evaluator
        self._on_reload = on_reload
        self._frame_cbs = []

    # ---- the signed call -----------------------------------------------------

    def call(self, method, url, params=None, body=None):
        if params:
            sep = "&" if "?" in url else "?"
            url = url + sep + urlencode(params)
        status, text = self._eval(method, url, params, body)
        return self._handle(status, text, url)

    def _handle(self, status, text, url):
        if status in (401, 407):
            raise errors.AuthError(f"http {status} on {_short(url)}")
        if status == 403:
            raise errors.Banned(f"http 403 on {_short(url)}")
        if status == 429:
            raise errors.RateLimited("http 429")
        if status and status >= 500:
            raise errors.Transient(f"http {status}")
        if status and status >= 400:
            raise errors.InvalidRequest(f"http {status}")
        if text is None or text == "":
            return {}
        try:
            data = json.loads(text)
        except ValueError:
            raise errors.SchemaChange(f"non-json body on {_short(url)}")
        self._check_body_code(data)
        return data

    @staticmethod
    def _check_body_code(data):
        if not isinstance(data, dict):
            return
        inner = data.get("data") if isinstance(data.get("data"), dict) else {}
        code = data.get("status_code")
        if code is None:
            code = inner.get("error_code")
        if code in AUTH_CODES:
            raise errors.AuthError("login expired", code=code)
        if code in RATE_CODES:
            raise errors.RateLimited("rate limited", code=code)

    # ---- realtime ------------------------------------------------------------

    def on_frame(self, cb):
        self._frame_cbs.append(cb)

    def _emit_frame(self, raw):
        for cb in self._frame_cbs:
            cb(raw)

    def reconnect(self):
        # reload /messages to re-open the frontier socket + re-run the signer.
        if self._on_reload:
            self._on_reload(MESSAGES_URL)


def _short(url):
    return url.split("?", 1)[0]


# ---- the real Playwright evaluator (used at runtime, not in tests) -----------

FETCH_JS = """([method, url, body]) =>
  fetch(url, {method, credentials: 'include',
              headers: body ? {'content-type': 'application/x-www-form-urlencoded'} : {},
              body: body || undefined})
    .then(r => r.text().then(t => [r.status, t]))"""


def playwright_evaluator(page):
    """Build an evaluator that runs the signed fetch inside a live Playwright page."""
    def ev(method, url, params, body):
        status, text = page.evaluate(FETCH_JS, [method, url, body])
        return status, text
    return ev
