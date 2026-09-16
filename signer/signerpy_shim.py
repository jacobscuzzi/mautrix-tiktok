import base64
import json
import sys

import SignerPy

def main():
    req = json.load(sys.stdin)
    query = req.get("query", "")
    body = base64.b64decode(req.get("body_b64", "") or "")
    cookie = req.get("cookie", "")
    sig = SignerPy.sign(params=query, data=body.decode("utf-8", "ignore"),
                        cookie=cookie, aid=1233)
    out = {}
    for k in ("x-gorgon", "x-khronos", "x-ladon", "x-argus", "x-ss-stub"):
        v = sig.get(k)
        if v:
            out[k] = str(v)
    json.dump(out, sys.stdout)

if __name__ == "__main__":
    main()
