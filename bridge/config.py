import base64
import os
import yaml

class Config:
    def __init__(self, master_key, signer_cmd, base_host, poll_interval_seconds,
                 session_dir, proxies):
        self.master_key = master_key
        self.signer_cmd = signer_cmd
        self.base_host = base_host
        self.poll_interval_seconds = poll_interval_seconds
        self.session_dir = session_dir
        self.proxies = proxies

    @classmethod
    def load(cls, path="config.yaml", environ=None):
        environ = environ if environ is not None else os.environ
        raw = {}
        if os.path.exists(path):
            with open(path) as f:
                raw = yaml.safe_load(f) or {}
        key_env = raw.get("master_key_env", "BRIDGE_MASTER_KEY")
        master_b64 = environ.get(key_env, "")
        if master_b64:
            master_key = base64.b64decode(master_b64)
            if len(master_key) != 32:
                raise ValueError("BRIDGE_MASTER_KEY must decode to 32 bytes")
        else:
            master_key = None  # caller decides: refuse in prod, ephemeral in dev
        proxies = raw.get("proxies")
        if proxies is None:
            single = raw.get("proxy") or environ.get("BRIDGE_PROXY", "")
            proxies = [single] if single else []
        return cls(
            master_key=master_key,
            signer_cmd=raw.get("signer_cmd"),
            base_host=raw.get("base_host", "api16-normal-c-useast2a.tiktokv.com"),
            poll_interval_seconds=int(raw.get("poll_interval_seconds", 30)),
            session_dir=raw.get("session_dir", "./sessions"),
            proxies=proxies,
        )
