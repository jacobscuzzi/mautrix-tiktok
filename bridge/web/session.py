"""WebSession: the web login's durable identity, and the cheapest liveness check.

Bridges Playwright `storage_state` (cookies + localStorage) and the fingerprint
`meta.json` to/from the encrypted `session_store` blob. The blob maps to bridgev2
`UserLogin.metadata`. Secrets live only inside the encrypted blob; nothing here
logs a cookie value.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .. import errors

# the cheapest logged-in check the capture shows (error_code 0 = alive)
ALIVE_PATH = "https://www.tiktok.com/passport/token/beat/web/"
MESSAGES_URL = "https://www.tiktok.com/messages"


@dataclass
class WebSession:
    cookies: list = field(default_factory=list)          # storage_state cookie dicts
    origins: list = field(default_factory=list)          # storage_state localStorage
    user_agent: str = ""
    locale: str = "en-US"
    timezone: str = "Europe/Paris"
    viewport: dict = field(default_factory=lambda: {"width": 1280, "height": 720})
    ttwid: str = ""
    sessionid: str = ""
    uid: str = ""
    region: str = ""
    proxy_id: str | None = None

    # ---- construction --------------------------------------------------------

    @classmethod
    def from_storage_state(cls, state, meta=None, region="", uid=""):
        meta = meta or {}
        cookies = state.get("cookies", [])
        jar = {c["name"]: c["value"] for c in cookies}
        return cls(
            cookies=cookies,
            origins=state.get("origins", []),
            user_agent=meta.get("ua", ""),
            locale=meta.get("lang", "en-US"),
            timezone=meta.get("tz", "Europe/Paris"),
            viewport={"width": int(meta.get("iw") or 1280),
                      "height": int(meta.get("ih") or 720)},
            ttwid=jar.get("ttwid", ""),
            sessionid=jar.get("sessionid", ""),
            uid=uid or jar.get("uid_tt", ""),
            region=region,
        )

    @classmethod
    def from_cookie_import(cls, payload):
        """mode='cookies': {cookies, user_agent, local_storage, device:{ttwid,...}}."""
        cookies = payload.get("cookies") or []
        if isinstance(cookies, dict):
            cookies = [{"name": k, "value": v, "domain": ".tiktok.com", "path": "/"}
                       for k, v in cookies.items()]
        jar = {c["name"]: c["value"] for c in cookies}
        device = payload.get("device") or {}
        return cls(
            cookies=cookies,
            origins=payload.get("local_storage") or [],
            user_agent=payload.get("user_agent", ""),
            ttwid=device.get("ttwid") or jar.get("ttwid", ""),
            sessionid=jar.get("sessionid", ""),
            uid=device.get("device_id") or jar.get("uid_tt", ""),
            region=device.get("region", ""),
        )

    # ---- session_store blob (encrypted at rest) ------------------------------

    def to_blob(self):
        return {
            "cookies": self.cookies, "origins": self.origins,
            "user_agent": self.user_agent, "locale": self.locale,
            "timezone": self.timezone, "viewport": self.viewport,
            "ttwid": self.ttwid, "sessionid": self.sessionid,
            "uid": self.uid, "region": self.region, "proxy_id": self.proxy_id,
        }

    @classmethod
    def from_blob(cls, blob):
        return cls(**{k: blob[k] for k in blob if k in cls.__dataclass_fields__})

    def storage_state(self):
        return {"cookies": self.cookies, "origins": self.origins}

    @property
    def is_logged_in(self):
        return bool(self.sessionid)

    def fingerprint_matches(self, other_ua):
        """Refuse a cookies-import that contradicts the profile's UA (takeover risk)."""
        return not (self.user_agent and other_ua and self.user_agent != other_ua)


def is_alive(caller):
    """Cheapest logged-in check. `caller` runs a GET and returns parsed JSON.

    Uses /passport/token/beat/web/ (error_code 0 = alive). Any auth-shaped signal
    (status_code 8 "Login expired", a /login redirect, error_code != 0) means dead.
    """
    try:
        data = caller("GET", ALIVE_PATH, {}, None)
    except errors.AuthError:
        return False
    if not isinstance(data, dict):
        return False
    inner = data.get("data") if isinstance(data.get("data"), dict) else data
    code = inner.get("error_code", data.get("status_code"))
    if code in (8,):  # "Login expired"
        return False
    return code in (0, None) and bool(inner.get("user_id_str") or data.get("message") == "success")
