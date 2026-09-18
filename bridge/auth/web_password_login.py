"""Username + password login as a real flow, ported from vaultbrowser's ladder.

The ladder is: restore session -> is_alive() -> alive: done; else exactly ONE
password login into TikTok's own form -> success: persist; challenge (captcha /
2FA / verify email / IDV): stop with needs_user, persist nothing new; wrong
credentials: bad_credentials, refuse further automatic attempts; locked: blocked.

Never retry a challenge. Never auto-loop logins. The browser work is behind a
`driver` so the state machine is unit-testable without a browser; the real driver
(`PlaywrightPasswordDriver`) pins selectors from the Task A capture in one place.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger("bridge.auth.web_password")

# Selectors pinned from browser-data/jakob DOM dumps, verified 2026-09-18. A
# missing selector is a schema change, not a crash.
SELECTORS = {
    "verified": "2026-09-18",
    "username": "input[name=\"username\"]",
    "password": "input[type=\"password\"]",
    "submit": "button[data-e2e=\"login-button\"]",
    "login_error": "[data-e2e=\"login-error\"]",
    "inbox": "[data-e2e=\"dm-conversation-list\"]",
    "locked": "[data-e2e=\"account-locked\"]",
    # any of these on the post-login page means a human challenge
    "challenge": ["[data-e2e=\"2fa-input\"]", "#captcha_container", ".captcha_verify_container",
                  "[data-e2e=\"verify-title\"]"],
}

# outcome states (align with the API status vocabulary)
CONNECTED = "connected"
NEEDS_USER = "needs_user"
BAD_CREDENTIALS = "bad_credentials"
BLOCKED = "blocked"
SCHEMA_CHANGE = "schema_change"


@dataclass
class LoginResult:
    kind: str          # success | challenge | bad_credentials | blocked | schema_change
    reason: str = ""   # for challenge: captcha | 2fa | verify_email | idv | selector_missing


@dataclass
class Outcome:
    state: str
    reason: str = ""
    logged_in: bool = False


class Ladder:
    def __init__(self, driver):
        self.driver = driver

    def connect(self, username=None, password=None):
        # 1. reuse the session if it is still alive (the common, quiet path).
        try:
            if self.driver.is_alive():
                return Outcome(CONNECTED, logged_in=True)
        except Exception as e:
            log.debug("is_alive failed, will try one login: %s", e)

        if not (username and password):
            return Outcome(NEEDS_USER, reason="no_session")

        # 2. exactly one password login. No retry on any non-success outcome.
        res = self.driver.password_login(username, password)
        if res.kind == "success":
            return Outcome(CONNECTED, logged_in=True)
        if res.kind == "challenge":
            return Outcome(NEEDS_USER, reason=res.reason or "challenge")
        if res.kind == "bad_credentials":
            return Outcome(BAD_CREDENTIALS, reason="wrong_password")
        if res.kind == "blocked":
            return Outcome(BLOCKED, reason="account_locked")
        return Outcome(SCHEMA_CHANGE, reason=res.reason or "selector_missing")


class PlaywrightPasswordDriver:
    """Drives a real persistent context against a login page. Selectors above."""

    def __init__(self, page, login_url, messages_url, session_check=None,
                 selectors=None):
        self.page = page
        self.login_url = login_url
        self.messages_url = messages_url
        self.session_check = session_check
        self.sel = selectors or SELECTORS

    def is_alive(self):
        if self.session_check:
            self.page.goto(self.session_check, wait_until="domcontentloaded")
            return '"alive": true' in self.page.content()
        self.page.goto(self.messages_url, wait_until="domcontentloaded")
        # a login redirect or a missing inbox means not alive
        if "/login" in self.page.url:
            return False
        return self.page.query_selector(self.sel["inbox"]) is not None

    def password_login(self, username, password):
        self.page.goto(self.login_url, wait_until="domcontentloaded")
        u = self.page.query_selector(self.sel["username"])
        p = self.page.query_selector(self.sel["password"])
        s = self.page.query_selector(self.sel["submit"])
        if not (u and p and s):
            return LoginResult("schema_change", "selector_missing")
        u.fill(username)
        p.fill(password)
        s.click()
        try:
            self.page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass
        return self._classify()

    def _classify(self):
        url = self.page.url
        if self.page.query_selector(self.sel["locked"]) or "/locked" in url:
            return LoginResult("blocked")
        for c in self.sel["challenge"]:
            if self.page.query_selector(c) or "/verify" in url:
                reason = ("2fa" if "2fa" in c or "verify" in url else
                          "captcha" if "captcha" in c else "idv")
                return LoginResult("challenge", reason)
        if self.page.query_selector(self.sel["login_error"]):
            return LoginResult("bad_credentials")
        if self.page.query_selector(self.sel["inbox"]) or "/messages" in url:
            return LoginResult("success")
        return LoginResult("schema_change", "unknown_post_login_page")
