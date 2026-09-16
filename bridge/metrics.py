def live_session_ratio(logins, now_ms, poll_interval_ms):
    if not logins:
        return 0.0
    live = sum(1 for l in logins
               if l.get("authenticated")
               and now_ms - l.get("last_sync_ms", 0) <= poll_interval_ms)
    return live / len(logins)

def reauth_share(logins):
    if not logins:
        return 0.0
    return sum(1 for l in logins if l.get("state") == "needs-reauth") / len(logins)
