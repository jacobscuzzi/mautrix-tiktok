"""TikAPI hosted-OAuth helper (the "buy" login flow).

TikAPI issues a per-user Account Key through a hosted authorization page, so the
bridge never sees TikTok credentials — the user authorizes on TikAPI's page and
we receive an `access_token` (the Account Key) on the redirect. This mirrors the
native QR flow's property (no password touches us) and maps to the same bridgev2
`LoginProcess` vocabulary: `display_and_wait` (send user to the URL) -> `complete`
(parse the redirect).

Confirmed params (docs, 2026-09-17): client_id (required), redirect_uri
(required), scope (space-separated, optional), state (optional), country
(optional ISO code), email (optional). Redirect returns access_token + scope.

DM-relevant scopes: view_messages, send_messages, conversation_requests,
view_notifications.
"""
from urllib.parse import urlencode, urlparse, parse_qs

AUTHORIZE_URL = "https://tikapi.io/account/authorize"

DM_SCOPES = [
    "view_messages",
    "send_messages",
    "conversation_requests",
    "view_notifications",
]


def authorize_url(client_id, redirect_uri, scope=DM_SCOPES, state=None,
                  country=None, email=None):
    if not client_id or not redirect_uri:
        raise ValueError("client_id and redirect_uri are required")
    params = {"client_id": client_id, "redirect_uri": redirect_uri}
    if scope:
        params["scope"] = " ".join(scope) if not isinstance(scope, str) else scope
    if state:
        params["state"] = state
    if country:
        params["country"] = country
    if email:
        params["email"] = email
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


def parse_redirect(redirect_url):
    """Extract the Account Key + granted scopes from the OAuth redirect.

    Returns {"account_key", "scope": [...], "state"}. TikAPI's implicit flow puts
    the token in the query string; some implicit flows use the URL fragment, so we
    read both. Raises ValueError if no token is present (e.g. user denied).
    """
    u = urlparse(redirect_url)
    q = parse_qs(u.query)
    if u.fragment:
        q.update(parse_qs(u.fragment))
    token = _first(q, "access_token") or _first(q, "account_key")
    if not token:
        err = _first(q, "error") or "no access_token in redirect"
        raise ValueError(f"tikapi oauth redirect carried no token: {err}")
    scope = _first(q, "scope") or ""
    return {
        "account_key": token,
        "scope": scope.split() if scope else [],
        "state": _first(q, "state"),
    }


def missing_scopes(granted, required=DM_SCOPES):
    """Which required scopes the user did not grant — a login-time guard."""
    g = set(granted or [])
    return [s for s in required if s not in g]


def _first(q, key):
    v = q.get(key)
    return v[0] if v else None
