from dataclasses import dataclass, field

@dataclass
class User:
    user_id: str
    display_name: str = ""
    avatar_url: str | None = None

@dataclass
class Thread:
    conversation_id: str
    participants: list = field(default_factory=list)
    is_stranger: bool = False

@dataclass
class Event:
    message_id: str
    conversation_id: str
    sender_id: str
    text: str
    timestamp_ms: int

def to_user(d):
    return User(
        user_id=str(d.get("uid") or d.get("user_id") or ""),
        display_name=d.get("nickname") or d.get("display_name") or "",
        avatar_url=d.get("avatar_url") or d.get("avatar_larger"),
    )

def to_thread(d, is_stranger=False):
    return Thread(
        conversation_id=str(d.get("conversation_id") or ""),
        participants=list(d.get("participants") or []),
        is_stranger=is_stranger,
    )

def to_event(d):
    return Event(
        message_id=str(d.get("server_message_id") or d.get("message_id") or ""),
        conversation_id=str(d.get("conversation_id") or ""),
        sender_id=str(d.get("sender") or d.get("sender_id") or ""),
        text=d.get("content") or "",
        timestamp_ms=int(d.get("create_time") or 0),
    )
