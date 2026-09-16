def read_varint(buf, i):
    shift = 0
    result = 0
    while True:
        b = buf[i]
        i += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, i
        shift += 7

def _write_varint(v):
    out = bytearray()
    while True:
        b = v & 0x7F
        v >>= 7
        if v:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)

def decode_fields(buf):
    fields = {}
    i = 0
    n = len(buf)
    while i < n:
        tag, i = read_varint(buf, i)
        field = tag >> 3
        wt = tag & 7
        if wt == 0:
            v, i = read_varint(buf, i)
        elif wt == 2:
            ln, i = read_varint(buf, i)
            v = buf[i:i + ln]
            i += ln
        else:
            break
        fields.setdefault(field, []).append(v)
    return fields

def encode_fields(fields):
    out = bytearray()
    for field, value in fields.items():
        if isinstance(value, int):
            out += _write_varint(field << 3)
            out += _write_varint(value)
        else:
            out += _write_varint((field << 3) | 2)
            out += _write_varint(len(value))
            out += value
    return bytes(out)
