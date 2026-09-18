"""Cookies import login (mode="cookies").

Accepts a session captured elsewhere -- the laptop -> server transfer (Task D) and
the future iPhone WKWebView path (Task F) -- and builds a WebSession. The backend
context's UA/locale/timezone/viewport are pinned to what was reported and the
reported ttwid is reused (never rotated). A UA that contradicts an existing
profile is refused with fingerprint_mismatch, never silently replaced.

Payload: {cookies, user_agent, local_storage, device: {ttwid, device_id, region}}.
"""
from __future__ import annotations

from .. import errors
from ..web.session import WebSession


def import_cookies(payload, existing=None):
    """Build a WebSession from a cookies payload.

    `existing` is the current WebSession for this user (from session_store), if any;
    a UA mismatch against it is refused. Returns the new WebSession.
    """
    if not payload.get("cookies"):
        raise errors.InvalidRequest("cookies import: no cookies supplied")
    ua = payload.get("user_agent", "")
    if existing is not None and not existing.fingerprint_matches(ua):
        raise errors.BridgeError(
            "fingerprint_mismatch: imported UA does not match the stored profile; "
            "refusing to rotate the device identity")
    session = WebSession.from_cookie_import(payload)
    if not session.is_logged_in:
        raise errors.AuthError("cookies import: no sessionid present")
    # carry the pinned fingerprint from an existing session when the import omits it
    if existing is not None:
        session.locale = session.locale or existing.locale
        session.timezone = session.timezone or existing.timezone
        session.viewport = session.viewport or existing.viewport
        session.proxy_id = existing.proxy_id
    return session
