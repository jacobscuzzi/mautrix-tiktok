"""One command: start the bridge and the tester wrapper, open the browser.

  ./bridge-app.sh                 # opens http://127.0.0.1:8770

Starts (a) the LiveBridge browser worker + the bridge HTTP API, and (b) the
wrapper UI that proxies to it. They are two programs sharing one process so the
demo is a single command; internally the wrapper is just a client of the bridge.
"""
from __future__ import annotations

import argparse
import os
import threading
import time
import webbrowser

from ..live import LiveBridge
from ..webapp import make_bridge_server


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--wrapper-port", type=int, default=8770)
    ap.add_argument("--bridge-port", type=int, default=8771)
    ap.add_argument("--headless", action="store_true",
                    help="run the login browser headless (no real login possible)")
    ap.add_argument("--no-open", action="store_true")
    ap.add_argument("--data-dir", default="browser-data/_live")
    a = ap.parse_args(argv)

    master_key = None
    env_key = os.environ.get("BRIDGE_MASTER_KEY")
    if env_key:
        import base64
        master_key = base64.b64decode(env_key)

    live = LiveBridge(data_dir=a.data_dir, master_key=master_key, headless=a.headless)
    live.start()

    bridge_srv = make_bridge_server(live, "127.0.0.1", a.bridge_port)
    threading.Thread(target=bridge_srv.serve_forever, daemon=True).start()

    from wrapper.server import make_wrapper_server
    wrapper_srv = make_wrapper_server(f"http://127.0.0.1:{a.bridge_port}",
                                      "127.0.0.1", a.wrapper_port)
    threading.Thread(target=wrapper_srv.serve_forever, daemon=True).start()

    url = f"http://127.0.0.1:{a.wrapper_port}"
    print(f"\n  TikTok DM Bridge tester\n"
          f"  wrapper UI : {url}\n"
          f"  bridge API : http://127.0.0.1:{a.bridge_port}  (/metrics for Prometheus)\n"
          f"  data dir   : {a.data_dir}  (sessions encrypted at rest; wiped on logout)\n"
          f"  Ctrl-C to stop.\n")
    if not a.no_open:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nstopping…")
        live.shutdown()
        bridge_srv.shutdown()
        wrapper_srv.shutdown()


if __name__ == "__main__":
    main()
