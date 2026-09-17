import base64
import os
import sys

from ..config import Config
from ..device import Device
from ..signing import NullSigner, SubprocessSigner
from ..client import Client
from ..proxy import ProxyPool
from ..session_store import SessionStore
from ..state import SyncState
from ..im import IM
from ..sync import Syncer
from ..app import BridgeApp
from .. import errors

# Maps a single live IM call to a named health state. This is where the IP
# problem shows up as a diagnosis rather than a crash: from a flagged IP the
# probe returns "rate_limited"; from a clean per-user proxy it reaches
# "im-not-initialized" or "connected".
def probe_status(client):
    try:
        client.get_im("/v1/client/unread_count/", {})
        return "connected"
    except errors.RateLimited:
        return "rate_limited"
    except errors.IMNotInitialized:
        return "im-not-initialized"
    except errors.AuthError:
        return "needs-reauth"
    except errors.Banned:
        return "banned"
    except errors.Transient:
        return "transient"

def _build(cfg, login_id="probe"):
    master = cfg.master_key
    if master is None:
        master = os.urandom(32)
        print("WARN: no BRIDGE_MASTER_KEY set, using an ephemeral dev key", file=sys.stderr)
    store = SessionStore(cfg.session_dir, master_key=master)
    signer = SubprocessSigner(cfg.signer_cmd) if cfg.signer_cmd else NullSigner()
    pool = ProxyPool(cfg.proxies)
    proxy = pool.for_login(login_id)
    if isinstance(signer, NullSigner):
        print("WARN: no signer configured, requests will be rejected by TikTok", file=sys.stderr)
    if not proxy:
        print("WARN: no proxy configured, egress uses this host's IP", file=sys.stderr)
    device = Device.generate(region=os.environ.get("BRIDGE_REGION", "US"))
    client = Client(device, signer, base_host=cfg.base_host, proxy=proxy)
    return store, client, device

def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    cmd = argv[0] if argv else "probe"
    cfg = Config.load()

    if cmd == "probe":
        _, client, _ = _build(cfg)
        print(f"proxy={'yes' if cfg.proxies else 'no'} signer={'yes' if cfg.signer_cmd else 'no'}")
        try:
            print("status:", probe_status(client))
        except Exception as e:
            print("status: error", type(e).__name__, str(e))
        return

    if cmd == "run":
        _, client, _ = _build(cfg, login_id=argv[1] if len(argv) > 1 else "run")
        state = SyncState(os.path.join(cfg.session_dir, "state.json"))
        state.load()
        im = IM(client)
        seen = []
        syncer = Syncer(im, state, seen.append)
        app = BridgeApp(syncer, cfg.poll_interval_seconds, lambda s: print("state:", s))
        print("running sync loop; Ctrl-C to stop")
        try:
            app.run()
        except KeyboardInterrupt:
            state.save()
            print("stopped, state saved")
        return

    print(f"unknown command: {cmd}; use 'probe' or 'run'", file=sys.stderr)
    sys.exit(2)

if __name__ == "__main__":
    main()
