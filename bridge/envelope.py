"""Mobile signed-API client -- documented alternative, not the shipped path (DESIGN.md §1)."""
from . import proto
from . import errors

def _first(fields, idx):
    v = fields.get(idx)
    return v[0] if v else None

def parse_response(body):
    f = proto.decode_fields(body)
    sc = _first(f, 3)
    desc = _first(f, 4)
    log_id = _first(f, 7)
    inner = _first(f, 6)
    return {
        "status_code": sc if isinstance(sc, int) else None,
        "error_desc": desc.decode() if isinstance(desc, bytes) else None,
        "log_id": log_id.decode() if isinstance(log_id, bytes) else None,
        "body": inner if isinstance(inner, bytes) else None,
        "raw": f,
    }

def classify(status_code, log_id):
    if status_code == 0:
        return None
    if status_code == 200001:
        raise errors.IMNotInitialized("im not initialized", code=status_code, log_id=log_id)
    if status_code in (200003, 200004, 200005):
        raise errors.AuthError("session invalid", code=status_code, log_id=log_id)
    raise errors.InvalidRequest("im request rejected", code=status_code, log_id=log_id)
