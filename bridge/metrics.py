def live_session_ratio(logins, now_ms, poll_interval_ms, grace=2.0):
    if not logins:
        return 0.0
    threshold = poll_interval_ms * grace
    live = sum(1 for l in logins
               if l.get("authenticated")
               and now_ms - l.get("last_sync_ms", 0) <= threshold)
    return live / len(logins)

def reauth_share(logins):
    if not logins:
        return 0.0
    return sum(1 for l in logins if l.get("state") == "needs-reauth") / len(logins)


def password_login_share(logins):
    """Leading indicator: share of connected logins that needed a password login.

    Rising = sessions dying early (they precede challenges and bans). A login dict
    carries `password_login_used` (bool) and `state`.
    """
    connected = [l for l in logins if l.get("state") in ("connected", "connecting")]
    if not connected:
        return 0.0
    return sum(1 for l in connected if l.get("password_login_used")) / len(connected)


def delivery_lag_p95(lags_seconds):
    """p95 of TikTok-ts -> ingested_at lag. Catches silent polling degradation."""
    xs = sorted(x for x in lags_seconds if x is not None)
    if not xs:
        return 0.0
    idx = min(len(xs) - 1, int(round(0.95 * (len(xs) - 1))))
    return float(xs[idx])


def render_prometheus(logins, now_ms, poll_interval_ms, error_totals=None,
                      lags_seconds=None):
    """Prometheus text exposition for /metrics."""
    lines = []

    def g(name, value, help_, kind="gauge"):
        lines.append(f"# HELP {name} {help_}")
        lines.append(f"# TYPE {name} {kind}")
        lines.append(f"{name} {value}")

    g("bridge_live_session_ratio",
      round(live_session_ratio(logins, now_ms, poll_interval_ms), 4),
      "connected+syncing logins / total (headline)")
    g("bridge_password_login_share", round(password_login_share(logins), 4),
      "share of connects that needed a password login (leading indicator)")
    g("bridge_reauth_share", round(reauth_share(logins), 4),
      "share of logins in needs-reauth")
    g("bridge_delivery_lag_seconds", round(delivery_lag_p95(lags_seconds or []), 3),
      "p95 TikTok-ts to ingested_at lag")
    g("bridge_logins_total", len(logins), "total logins")
    for state, n in sorted((error_totals or {}).items()):
        lines.append("# TYPE bridge_error_total counter")
        lines.append(f'bridge_error_total{{state="{state}"}} {n}')
    return "\n".join(lines) + "\n"
