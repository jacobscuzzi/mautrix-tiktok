import random
import time
from dataclasses import dataclass

AID = "1233"
APP_NAME = "musical_ly"
APP_VERSION = "46.5.0"
VERSION_CODE = "460500"
MANIFEST_VERSION_CODE = "2024605000"
MODELS = ["SM-S928B", "SM-S926B", "Pixel 9 Pro", "Pixel 8"]

def _digits(n):
    return "".join(random.choices("0123456789", k=n))

def _hex(n):
    return "".join(random.choices("0123456789abcdef", k=n))

_COOKIE_MAP = {
    "sessionid": "sessionid", "sessionid_ss": "sessionid",
    "sid_tt": "sid_tt", "sid_guard": "sid_guard",
    "uid_tt": "uid_tt", "install_id": "install_id",
}

@dataclass
class Device:
    device_id: str = ""
    install_id: str = ""
    openudid: str = ""
    cdid: str = ""
    device_type: str = "SM-S928B"
    device_brand: str = "samsung"
    os_version: str = "14"
    region: str = "US"
    language: str = "en"
    app_version: str = APP_VERSION
    version_code: str = VERSION_CODE
    manifest_version_code: str = MANIFEST_VERSION_CODE
    sessionid: str = ""
    sid_tt: str = ""
    sid_guard: str = ""
    uid_tt: str = ""
    user_id: str = ""

    @classmethod
    def generate(cls, region="US"):
        model = random.choice(MODELS)
        return cls(
            device_id=_digits(19),
            install_id=_digits(19),
            openudid=_hex(16),
            cdid=_hex(16),
            device_type=model,
            device_brand="google" if model.startswith("Pixel") else "samsung",
            region=region,
        )

    @property
    def user_agent(self):
        return (f"com.zhiliaoapp.musically/{self.manifest_version_code} "
                f"(Linux; U; Android {self.os_version}; {self.language}; "
                f"{self.device_type}; Build/UE1A.230829.050; Cronet/TTNetVersion:45466851)")

    @property
    def is_authenticated(self):
        return bool(self.sessionid)

    def import_cookies(self, raw):
        for part in raw.split(";"):
            if "=" not in part:
                continue
            k, _, v = part.strip().partition("=")
            attr = _COOKIE_MAP.get(k.strip())
            if attr and v:
                setattr(self, attr, v.strip())

    def cookie_string(self):
        parts = [f"install_id={self.install_id}"]
        for k in ("sessionid", "sid_tt", "sid_guard", "uid_tt"):
            v = getattr(self, k)
            if v:
                parts.append(f"{k}={v}")
        return "; ".join(parts)

    def common_params(self):
        now = int(time.time())
        return {
            "device_platform": "android", "os": "android",
            "aid": AID, "app_name": APP_NAME,
            "version_code": self.version_code, "version_name": self.app_version,
            "manifest_version_code": self.manifest_version_code,
            "device_type": self.device_type, "device_brand": self.device_brand,
            "os_version": self.os_version, "region": self.region,
            "sys_region": self.region, "language": self.language,
            "device_id": self.device_id, "iid": self.install_id,
            "openudid": self.openudid, "cdid": self.cdid,
            "ts": str(now), "_rticket": str(now * 1000),
        }
