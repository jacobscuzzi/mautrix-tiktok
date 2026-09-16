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
