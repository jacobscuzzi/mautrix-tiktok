"""Message provider seam — the build-vs-buy boundary.

`Syncer` (bridge/sync.py) already depends only on a duck-typed *fetcher* with
`list_conversations(cursor)` and `get_messages(conv_id, cursor)`, each returning
`(items, next_cursor, has_more)`, plus `send_text` / `mark_read` for the outbound
side. This module names that contract as a `Protocol` so the three ways of getting
TikTok DMs are interchangeable and independently testable:

- `WebProvider`     — the shipped path (bridge/providers/web.py): a real browser per
  login runs TikTok's own web signer and the bridge taps its traffic.
- `NativeProvider`  — the self-signed mobile protobuf client (bridge/im.py `IM`).
  Full control, zero per-message cost, but we own the signing arms race and the
  ban risk. Documented alternative; not runnable from a datacenter (DESIGN.md §1).
- `TikApiProvider`  — delegate to a third-party API vendor (TikAPI). We stop
  maintaining the signer and proxies; in exchange we take on a paid vendor
  dependency, their rate limits, and their terms. Implemented in
  bridge/providers/tikapi.py.

Mapping to bridgev2: a `MessageProvider` is one login's `NetworkAPI`. Swapping the
provider swaps the whole transport/signing/ingest stack below `Syncer` without
touching normalization, state, dedup, or metrics.

`items` are plain dicts; `bridge/normalize.py` already accepts the union of native
and vendor key names, so a provider only has to hand back dicts with recognizable
keys (`conversation_id`, `message_id`/`server_message_id`, `sender`/`sender_id`,
`content`, `create_time`, ...).
"""
from typing import Protocol, runtime_checkable


@runtime_checkable
class MessageProvider(Protocol):
    def list_conversations(self, cursor: str = "0", count: int = 20):
        """Return (conversation_dicts, next_cursor, has_more)."""
        ...

    def get_messages(self, conv_id: str, cursor: str = "0", count: int = 20):
        """Return (message_dicts, next_cursor, has_more)."""
        ...

    def send_text(self, conv_id: str, text: str, client_message_id: str = ""):
        """Send a text message. Must be idempotent on client_message_id."""
        ...

    def mark_read(self, conv_id: str):
        ...


# The native IM object already satisfies MessageProvider structurally. This alias
# documents that and gives callers one import for the mobile option.
from .im import IM as NativeProvider  # noqa: E402


def describe(provider) -> str:
    """Human label for logs/metrics without leaking a session."""
    cls = type(provider).__name__
    return {"IM": "native", "TikApiProvider": "tikapi",
            "WebProvider": "web"}.get(cls, cls)
