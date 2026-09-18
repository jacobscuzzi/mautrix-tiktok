import json
from dataclasses import dataclass, field

# Canonical records shared by every provider (native mobile, web, tikapi). The
# bridgev2 mapping: Thread -> Portal, User -> Ghost, Event -> Message. Fields are
# additive with defaults so the existing mobile/tikapi callers stay unchanged.


@dataclass
class User:
    user_id: str
    display_name: str = ""
    avatar_url: str | None = None
    handle: str = ""
    sec_uid: str = ""


@dataclass
class Thread:
    conversation_id: str
    participants: list = field(default_factory=list)
    is_stranger: bool = False
    thread_type: str = "dm"
    last_ts: int = 0


@dataclass
class Event:
    message_id: str
    conversation_id: str
    sender_id: str
    text: str
    timestamp_ms: int
    kind: str = "text"
    raw_ref: str | None = None


# TikTok DM `content` is often a JSON string like {"text":"hi","aweType":700}.
def extract_text(content):
    if content is None:
        return ""
    if isinstance(content, dict):
        return content.get("text") or content.get("content") or ""
    if isinstance(content, (bytes, bytearray)):
        content = content.decode("utf-8", "replace")
    if isinstance(content, str):
        s = content.strip()
        if s.startswith("{"):
            try:
                return extract_text(json.loads(s))
            except ValueError:
                return content
        return content
    return str(content)


# message_type -> canonical kind. 1=text; the rest are best-effort [Inf] from the
# mobile IM SDK and the webcast findings, refined when a real DM of each is seen.
_KIND = {1: "text", 2: "image", 5: "video", 7: "share", 8: "share"}


def _kind_of(d, text):
    mt = d.get("message_type") or d.get("type")
    if mt in _KIND:
        return _KIND[mt]
    if not text and (d.get("aweme") or d.get("share")):
        return "share"
    return "text"


def _avatar_from(d):
    # accept a plain url, an {url_list:[...]} object, or avatar_thumb/avatar_medium.
    for key in ("avatar_url", "avatar_larger", "avatar_thumb", "avatar_medium",
                "avatar_168x168", "avatar"):
        v = d.get(key)
        if isinstance(v, str) and v:
            return v
        if isinstance(v, dict):
            urls = v.get("url_list") or []
            if urls:
                return urls[0]
    avatars = d.get("avatars")
    if isinstance(avatars, dict):
        for k in ("avatar_medium", "avatar_small"):
            urls = (avatars.get(k) or {}).get("url_list") or []
            if urls:
                return urls[0]
    return None


def to_user(d):
    return User(
        user_id=str(d.get("uid") or d.get("user_id") or d.get("user_id_str") or ""),
        display_name=d.get("nickname") or d.get("nick_name") or d.get("display_name") or "",
        avatar_url=_avatar_from(d),
        handle=d.get("unique_id") or d.get("handle") or "",
        sec_uid=d.get("sec_uid") or d.get("secUid") or "",
    )


def to_thread(d, is_stranger=False):
    return Thread(
        conversation_id=str(d.get("conversation_id") or d.get("id") or ""),
        participants=list(d.get("participants") or d.get("members") or []),
        is_stranger=bool(is_stranger or d.get("is_stranger") or d.get("is_request")),
        thread_type="group" if (d.get("conversation_type") == 2) else "dm",
        last_ts=int((d.get("last_message") or {}).get("create_time") or d.get("last_ts") or 0),
    )


def to_event(d):
    text = extract_text(d.get("content") if d.get("content") is not None else d.get("text"))
    return Event(
        message_id=str(d.get("server_message_id") or d.get("message_id") or ""),
        conversation_id=str(d.get("conversation_id") or ""),
        sender_id=str(d.get("sender") or d.get("sender_id") or ""),
        text=text,
        timestamp_ms=int(d.get("create_time") or d.get("timestamp_ms") or 0),
        kind=_kind_of(d, text),
        raw_ref=d.get("raw_ref"),
    )
